"""Observe the executable bytes of one live isolated role through Linux procfs.

This is a bounded execution snapshot, not an all-exec or Secret trace. The
caller supplies an expected digest, but the producer hashes the open procfs
executable and checks the process and inode on both sides of that read. Raw
PID, process name, paths and inode metadata never enter the returned record.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import stat
from pathlib import Path
from typing import Any

from benchmarks.hosts.linux_guest_observer import (
    ObservationGap,
    _namespace_digest,
    _read_bounded_file,
)

MAX_EXECUTABLE_BYTES = 512 * 1024 * 1024
_DIGEST = re.compile(r"[0-9a-f]{64}")
_ROLE_UIDS = {"host": 1000, "mcp": 1001}


def _digest(value: Any) -> str:
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()
    ).hexdigest()


def _process_metadata(proc_dir: Path, pid: int) -> dict[str, Any]:
    raw = _read_bounded_file(proc_dir / "stat", limit=4096)
    prefix, separator, tail = raw.rpartition(b") ")
    try:
        # comm may contain spaces and parentheses; consume it without retaining it.
        fields = tail.split()
        if not separator or int(prefix.split(b" ", 1)[0]) != pid or len(fields) < 20:
            raise ValueError
        start_ticks = int(fields[19])  # field 22, after pid and comm
        if start_ticks <= 0 or fields[0] in {b"Z", b"X", b"x"}:
            raise ValueError
    except (IndexError, ValueError) as error:
        raise ObservationGap("execution_process_stat_invalid") from error
    status = _read_bounded_file(proc_dir / "status", limit=16 * 1024)
    uid_rows = [line.split()[1:] for line in status.splitlines() if line.startswith(b"Uid:")]
    try:
        if len(uid_rows) != 1 or len(uid_rows[0]) != 4:
            raise ValueError
        uids = tuple(int(value) for value in uid_rows[0])
        if len(set(uids)) != 1:
            raise ValueError
    except ValueError as error:
        raise ObservationGap("execution_process_uid_invalid") from error
    return {
        "pid": pid,
        "start_ticks": start_ticks,
        "uid": uids[0],
        "namespaces": {
            name: _namespace_digest(proc_dir, name) for name in ("mnt", "pid", "net", "ipc")
        },
    }


def _inode_metadata(details: os.stat_result) -> tuple[int, ...]:
    return (
        details.st_dev, details.st_ino, details.st_mode, details.st_nlink,
        details.st_size, details.st_mtime_ns, details.st_ctime_ns,
    )


def _open_executable(proc_dir: Path):
    # /proc/PID/exe is intentionally a kernel magic link to the executed inode.
    # O_NOFOLLOW would reject the very observation required here.
    return (proc_dir / "exe").open("rb")


def observe_process_start_identity(*, role: str, pid: int) -> str:
    """Commit the launcher's live role PID and birth metadata before exec."""
    if not isinstance(role, str) or role not in _ROLE_UIDS or type(pid) is not int or pid <= 1:
        raise ObservationGap("execution_role_invalid")
    metadata = _process_metadata(Path("/proc") / str(pid), pid)
    if metadata["uid"] != _ROLE_UIDS[role]:
        raise ObservationGap("execution_role_uid_mismatch")
    return _digest({"role": role, **metadata})


def observe_executed_binary(
    *, role: str, pid: int, expected_sha256: str, binding_sha256: str,
) -> dict[str, Any]:
    """Require a stable live process and actual expected executable bytes."""
    if not isinstance(role, str) or role not in _ROLE_UIDS or type(pid) is not int or pid <= 1:
        raise ObservationGap("execution_role_invalid")
    for value in (expected_sha256, binding_sha256):
        if not isinstance(value, str) or _DIGEST.fullmatch(value) is None or value == "0" * 64:
            raise ObservationGap("execution_digest_invalid")
    proc_dir = Path("/proc") / str(pid)
    try:
        before = _process_metadata(proc_dir, pid)
        if before["uid"] != _ROLE_UIDS[role]:
            raise ObservationGap("execution_role_uid_mismatch")
        with _open_executable(proc_dir) as executable:
            first = os.fstat(executable.fileno())
            if (
                not stat.S_ISREG(first.st_mode) or first.st_nlink != 1
                or not 0 < first.st_size <= MAX_EXECUTABLE_BYTES
            ):
                raise ObservationGap("execution_inode_invalid")
            digest = hashlib.sha256()
            measured_bytes = 0
            while block := executable.read(min(1024 * 1024, first.st_size - measured_bytes + 1)):
                measured_bytes += len(block)
                if measured_bytes > first.st_size:
                    raise ObservationGap("execution_inode_changed")
                digest.update(block)
            if measured_bytes != first.st_size:
                raise ObservationGap("execution_inode_changed")
            measured = digest.hexdigest()
            if _inode_metadata(first) != _inode_metadata(os.fstat(executable.fileno())):
                raise ObservationGap("execution_inode_changed")
            with _open_executable(proc_dir) as current:
                if _inode_metadata(first) != _inode_metadata(os.fstat(current.fileno())):
                    raise ObservationGap("execution_target_changed")
        if _process_metadata(proc_dir, pid) != before:
            raise ObservationGap("execution_process_changed")
        if measured != expected_sha256:
            raise ObservationGap("execution_bytes_mismatch")
    except ObservationGap:
        raise
    except (OSError, ValueError) as error:
        raise ObservationGap("execution_metadata_unavailable") from error
    record = {
        "schema_version": "deeplaw.linux-execution-snapshot/v1",
        "status": "observed",
        "formal_admission": False,
        "claim_eligible": False,
        "source": "linux_proc_exe",
        "role": role,
        "uid": before["uid"],
        "binding_sha256": binding_sha256,
        "executable_sha256": measured,
        "executable_bytes": first.st_size,
        "execution_target_regular": True,
        "execution_target_single_link": True,
        "process_identity_sha256": _digest({
            **before, "binding_sha256": binding_sha256,
            "inode": _inode_metadata(first), "executable_sha256": measured,
        }),
        "process_start_identity_sha256": _digest({"role": role, **before}),
        "namespaces": before["namespaces"],
    }
    record["record_sha256"] = _digest(record)
    return record


def validate_execution_snapshot(value: Any) -> dict[str, Any]:
    """Reopen a closed snapshot without promoting its observation authority."""
    keys = {
        "schema_version", "status", "formal_admission", "claim_eligible", "source",
        "role", "uid", "binding_sha256", "executable_sha256", "executable_bytes",
        "execution_target_regular", "execution_target_single_link",
        "process_identity_sha256", "process_start_identity_sha256", "namespaces", "record_sha256",
    }
    if not isinstance(value, dict) or set(value) != keys:
        raise ObservationGap("execution_snapshot_invalid")
    if (
        value["schema_version"] != "deeplaw.linux-execution-snapshot/v1"
        or value["status"] != "observed" or value["source"] != "linux_proc_exe"
        or value["formal_admission"] is not False or value["claim_eligible"] is not False
        or not isinstance(value["role"], str) or value["role"] not in _ROLE_UIDS
        or type(value["uid"]) is not int
        or value["uid"] != _ROLE_UIDS[value["role"]]
        or value["execution_target_regular"] is not True
        or value["execution_target_single_link"] is not True
        or type(value["executable_bytes"]) is not int
        or not 0 < value["executable_bytes"] <= MAX_EXECUTABLE_BYTES
        or not isinstance(value["namespaces"], dict)
        or set(value["namespaces"]) != {"mnt", "pid", "net", "ipc"}
    ):
        raise ObservationGap("execution_snapshot_invalid")
    for digest in (
        value["binding_sha256"], value["executable_sha256"],
        value["process_identity_sha256"], value["record_sha256"],
        value["process_start_identity_sha256"],
        *value["namespaces"].values(),
    ):
        if not isinstance(digest, str) or _DIGEST.fullmatch(digest) is None or digest == "0" * 64:
            raise ObservationGap("execution_snapshot_invalid")
    body = {key: item for key, item in value.items() if key != "record_sha256"}
    if value["record_sha256"] != _digest(body):
        raise ObservationGap("execution_snapshot_digest_mismatch")
    # The fresh JSON copy detaches the caller's nested dictionaries.
    return json.loads(json.dumps(value))
