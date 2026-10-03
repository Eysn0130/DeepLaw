"""Stage exact public C6 engineering bytes, without admitting native Authority.

This is an opt-in attachment seam for a future Kernel bundle consumer. It does
not activate a bundle version or contribute a Commercial pass. The owner must
serialize source and destination mutations while staging. Failure preserves the
source and any partial destination; retry requires a new destination.
"""

from __future__ import annotations

import os
import stat
from pathlib import Path, PurePosixPath
from typing import Any

from jsonschema import Draft202012Validator

from benchmarks.hosts import maintenance_run_evidence as consumer
from benchmarks.hosts.maintenance_context_snapshot import _directory, _identity, _read
from benchmarks.hosts.maintenance_task_cases import CONFIGURATION_ORDER, SCENARIO_ORDER
from deeplaw.util import canonical_json, sha256_bytes, strict_json_loads

SCHEMA_VERSION = "deeplaw.maintenance-bundle-attachment/v1"
ATTACHMENT_SUBTREE = "attachments/c6_public_maintenance"
MAX_ATTACHMENT_FILES = consumer._MAX_USED_FILES
MAX_ATTACHMENT_BYTES = consumer._MAX_USED_BYTES
SCHEMA_PATH = Path(__file__).resolve().parents[2] / (
    "contracts/maintenance-bundle-attachment.v1.schema.json"
)
_EMPTY_ROOTS = ("sources", ".deeplaw/capabilities", ".deeplaw/staging")
GENERATED_EMPTY_DIRECTORIES = tuple(
    f"context-snapshots/{configuration}-{scenario}/vault/{relative}"
    for configuration in CONFIGURATION_ORDER
    for scenario in SCENARIO_ORDER
    for relative in _EMPTY_ROOTS
)
_CAPTION = (
    "Exact public engineering attachment only. Host/model execution remains producer "
    "declarations; native and final-artifact Authority and Commercial admission "
    "are not established."
)


def _digest(value: Any) -> str:
    return sha256_bytes(canonical_json(value).encode())


def record_sha256(value: dict) -> str:
    """Bind the closed receipt body, excluding its self digest."""
    return _digest({key: item for key, item in value.items() if key != "record_sha256"})


def _slots(validated: dict) -> list[dict]:
    return [{
        "configuration_id": slot["configuration_id"], "scenario_id": slot["scenario_id"],
        "run_id": slot["run_id"], "producer_status": slot["producer_status"],
        "score_sha256": _digest(slot["score"]), **{key: slot["score"][key] for key in (
            "passed", "unknown_outcome", "outcome_unknown", "safe_termination", "event_count",
            "final_state_sha256", "failure_codes",
        )},
    } for slot in validated["slots"]]


def _summary(slots: list[dict]) -> dict:
    return {
        "slot_count": 15, "passed_count": sum(slot["passed"] for slot in slots),
        "failed_count": sum(not slot["passed"] for slot in slots),
        "unknown_count": sum(slot["unknown_outcome"] for slot in slots),
        "producer_succeeded_count": sum(slot["producer_status"] == "succeeded" for slot in slots),
        "producer_failed_count": sum(slot["producer_status"] == "failed" for slot in slots),
    }


def validate_attachment_receipt(value: dict) -> None:
    """Validate shape and cross-field bindings, without granting artifact Authority."""
    try:
        schema = strict_json_loads(SCHEMA_PATH.read_bytes())
        Draft202012Validator(schema).validate(value)
        files = value["source_files"]
        paths = [item["path"] for item in files]
        if (paths != sorted(set(paths))
                or sum(item["size"] for item in files) > MAX_ATTACHMENT_BYTES
                or value["closure_sha256"] != _digest(files)
                or value["record_sha256"] != record_sha256(value)
                or value["summary"] != _summary(value["slots"])
                or value["generated_empty_directories"] != list(GENERATED_EMPTY_DIRECTORIES)
                or any(slot["run_id"] != value["run_id"] + "-" + slot["configuration_id"]
                       + "-" + slot["scenario_id"] for slot in value["slots"])):
            raise ValueError("attachment receipt binding differs")
    except Exception:
        raise ValueError("maintenance attachment receipt validation failed") from None


def _path(value: Path) -> Path:
    if not isinstance(value, Path):
        raise ValueError("attachment root differs")
    result = value.absolute()
    if ".." in result.parts or len(result.parts) > 128:
        raise ValueError("attachment root differs")
    return result


def _directory_identity(value: os.stat_result) -> tuple:
    if (not stat.S_ISDIR(value.st_mode) or value.st_mode & 0o077
            or value.st_uid != os.geteuid()):
        raise ValueError("attachment directory differs")
    # Child creation intentionally changes directory timestamps and link counts.
    return value.st_dev, value.st_ino, value.st_mode, value.st_uid


def _create_destination(destination: Path, directories: set[str]) -> dict[str, tuple]:
    parent_fd, _ = _directory(destination.parent, ".")
    try:
        parent_identity = _directory_identity(os.fstat(parent_fd))
        os.mkdir(destination.name, mode=0o700, dir_fd=parent_fd)
        if _directory_identity(destination.parent.lstat()) != parent_identity:
            raise ValueError("attachment parent changed")
    finally:
        os.close(parent_fd)
    identities = {".": _directory_identity(destination.lstat())}
    for relative in sorted(directories - {"."}, key=lambda item: (len(item.split("/")), item)):
        path = PurePosixPath(relative)
        parent_fd, _ = _directory(destination, path.parent.as_posix())
        try:
            os.mkdir(path.name, mode=0o700, dir_fd=parent_fd)
            identities[relative] = _directory_identity(
                os.stat(path.name, dir_fd=parent_fd, follow_symlinks=False),
            )
        finally:
            os.close(parent_fd)
    return identities


def _check_directories(root: Path, identities: dict[str, tuple]) -> None:
    for relative, identity in identities.items():
        if _directory_identity((root / relative).lstat()) != identity:
            raise ValueError("attachment directory changed")


def _write_original(root: Path, relative: str, raw: bytes) -> tuple:
    path = PurePosixPath(relative)
    parent_fd, _ = _directory(root, path.parent.as_posix())
    descriptor = None
    try:
        descriptor = os.open(
            path.name, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW | os.O_NONBLOCK,
            0o600, dir_fd=parent_fd,
        )
        opened = os.fstat(descriptor)
        if (not stat.S_ISREG(opened.st_mode) or opened.st_mode & 0o077
                or opened.st_uid != os.geteuid() or opened.st_nlink != 1 or opened.st_size != 0):
            raise ValueError("attachment output file differs")
        pending = memoryview(raw)
        while pending:
            written = os.write(descriptor, pending)
            if written <= 0:
                raise ValueError("attachment write incomplete")
            pending = pending[written:]
        os.fsync(descriptor)
        after = os.fstat(descriptor)
        current = os.stat(path.name, dir_fd=parent_fd, follow_symlinks=False)
        if (after.st_dev != opened.st_dev or after.st_ino != opened.st_ino
                or after.st_mode != opened.st_mode or after.st_uid != opened.st_uid
                or after.st_nlink != 1 or after.st_size != len(raw)
                or _identity(current) != _identity(after)):
            raise ValueError("attachment output file changed")
    finally:
        if descriptor is not None:
            os.close(descriptor)
        os.close(parent_fd)
    copied, identity = _read(root, relative, len(raw))
    if copied != raw or identity != _identity(after):
        raise ValueError("attachment copied bytes differ")
    return identity


def _destination_closed(root: Path, files: list[dict], identities: dict[str, tuple]) -> None:
    children = {relative: set() for relative in identities}
    for relative in [*(item["path"] for item in files), *(identities.keys() - {"."})]:
        path = PurePosixPath(relative)
        children[path.parent.as_posix()].add(path.name)
    for relative, expected in children.items():
        descriptor, _ = _directory(root, relative)
        try:
            with os.scandir(descriptor) as entries:
                # Limit discovery even if another writer added many files.
                actual = set()
                for entry in entries:
                    actual.add(entry.name)
                    if len(actual) > len(expected):
                        raise ValueError("attachment destination closure differs")
            if actual != expected:
                raise ValueError("attachment destination closure differs")
        finally:
            os.close(descriptor)
    _check_directories(root, identities)


def _stage(source_root: Path, destination: Path, **expected: str) -> dict:
    if os.name != "posix":
        raise ValueError("attachment staging requires POSIX")
    source_root, destination = _path(source_root), _path(destination)
    if (destination.parts[-2:] != tuple(ATTACHMENT_SUBTREE.split("/"))
            or source_root == destination or destination.is_relative_to(source_root)):
        raise ValueError("attachment destination overlaps source")
    validated = consumer.validate_run_evidence(source_root, **expected)
    files = validated["files"]
    if len(files) > MAX_ATTACHMENT_FILES or sum(item["size"] for item in files) > (
        MAX_ATTACHMENT_BYTES
    ):
        raise ValueError("attachment closure exceeds its bound")
    before = {}
    directories = {".", *GENERATED_EMPTY_DIRECTORIES}
    for reference in files:
        relative = reference["path"]
        path = PurePosixPath(relative)
        directories.update(parent.as_posix() for parent in path.parents)
        raw, identity = _read(source_root, relative, reference["size"])
        if len(raw) != reference["size"] or sha256_bytes(raw) != reference["sha256"]:
            raise ValueError("attachment source bytes differ")
        before[relative] = identity
    for relative in GENERATED_EMPTY_DIRECTORIES:
        directories.update(parent.as_posix() for parent in PurePosixPath(relative).parents)
    destination_directories = _create_destination(destination, directories)
    copied_identities = {}
    for reference in files:
        relative = reference["path"]
        raw, identity = _read(source_root, relative, reference["size"])
        if (identity != before[relative] or len(raw) != reference["size"]
                or sha256_bytes(raw) != reference["sha256"]):
            raise ValueError("attachment source changed during copy")
        _check_directories(destination, destination_directories)
        copied_identities[relative] = _write_original(destination, relative, raw)
    _destination_closed(destination, files, destination_directories)
    if consumer.validate_run_evidence(destination, **expected) != validated:
        raise ValueError("attachment reopened evidence differs")
    for reference in files:
        relative = reference["path"]
        for root, identities in ((source_root, before), (destination, copied_identities)):
            raw, identity = _read(root, relative, reference["size"])
            if (identity != identities[relative] or len(raw) != reference["size"]
                    or sha256_bytes(raw) != reference["sha256"]):
                raise ValueError("attachment retained bytes changed")
    _destination_closed(destination, files, destination_directories)
    slots = _slots(validated)
    receipt = {
        "schema_version": SCHEMA_VERSION, "attachment_subtree": ATTACHMENT_SUBTREE,
        **{key: validated[key] for key in (
            "run_id", "candidate_id", "binary_sha256", "public_input_sha256",
            "execution_evidence", "formal_admission",
        )},
        "source_files": files, "closure_sha256": _digest(files), "slots": slots,
        "summary": _summary(slots),
        "generated_empty_directories": list(GENERATED_EMPTY_DIRECTORIES),
        "directory_topology": "derived_fixed_empty_directories",
        "claim_scope": "public_engineering_attachment_only", "caption": _CAPTION,
    }
    receipt["record_sha256"] = record_sha256(receipt)
    validate_attachment_receipt(receipt)
    return receipt


def stage_public_maintenance_attachment(
    source_root: Path, destination: Path, *, run_id: str, candidate_id: str, binary_sha256: str,
) -> dict[str, Any]:
    """Revalidate and exclusively copy the consumed original public byte closure.

    The three identity arguments are caller-supplied expected identities. Pass a
    new destination under the bundle's attachments/c6_public_maintenance path;
    its parent must already be an owner-only directory. Only the validator's
    source file references are read or copied. Fixed empty directory topology is
    generated locally so the copied fixture can be independently reopened.
    """
    try:
        return _stage(source_root, destination, run_id=run_id, candidate_id=candidate_id,
                      binary_sha256=binary_sha256)
    except Exception:
        raise ValueError("maintenance attachment staging failed") from None
