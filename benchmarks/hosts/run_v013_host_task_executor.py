"""Admit one owner-external v0.13 six-slot Host task staging tree.

This module does not start a Host, invoke a model, read credentials, or create
qualification evidence.  It reopens an already-produced owner-external tree
through the public typed-evidence and Host-receipt validators, then promotes
the exact bytes only after all six frozen Host/task slots pass together.
"""

from __future__ import annotations

import argparse
import ctypes
import errno
import hashlib
import json
import os
import re
import shutil
import stat
import sys
import tempfile
from collections.abc import Mapping
from pathlib import Path, PurePosixPath
from typing import Any

from benchmarks.hosts import host_preflight_receipt, host_process_receipt_v2
from benchmarks.hosts.run_v013_host_task_qualification import (
    HOSTS,
    TASK_CASES,
    TYPED_SOURCE_SLOTS,
    HostTaskQualificationError,
    load_exact_candidate_binding,
    validate_external_collector_handoff,
    validate_host_task_matrix,
    validate_retained_manifest,
)
from deeplaw.util import strict_json_loads

REPOSITORY = Path(__file__).resolve().parents[2]
TYPED_SCHEMA = "deeplaw.typed-qualification-evidence/v3"
EXECUTION_IDENTITY_SCHEMA = "deeplaw.host-execution-identity/v1"
MAX_FILE_BYTES = 64 * 1024 * 1024
MAX_TOTAL_BYTES = 512 * 1024 * 1024
BROKER_SOURCE_MAX_BYTES = 256 * 1024
_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_BROKER_SECRET_LITERAL = re.compile(
    rb"(?i)(?:api[_-]?key|access[_-]?token|authorization|bearer|password|secret)"
    rb"\s*[:=]\s*[\"']?[A-Za-z0-9+/_-]{20,}"
)
_FORBIDDEN_NAME = re.compile(
    r"(?:^|[._-])(?:auth|credentials?|secrets?|passwords?|api[_-]?keys?|"
    r"private[_-]?keys?|tokens?|prompts?|transcripts?|reasoning|stdout|stderr|"
    r"dotenv|env)(?:$|[._-])",
    re.IGNORECASE,
)


class HostTaskExecutorError(RuntimeError):
    """The external six-slot staging tree is incomplete or unsafe."""


class HostTaskPromotionCommittedError(HostTaskExecutorError):
    """No-replace rename committed, but parent durability was not confirmed."""


def _fail(message: str) -> None:
    raise HostTaskExecutorError(message)


def _sha256(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def _stat_signature(details: os.stat_result) -> tuple[int, int, int, int, int, int]:
    return (
        details.st_dev,
        details.st_ino,
        details.st_mode,
        details.st_size,
        details.st_mtime_ns,
        details.st_ctime_ns,
    )


def _strict_object(path: Path, *, label: str) -> dict[str, Any]:
    try:
        raw = _read_stable_file(path, maximum_bytes=MAX_FILE_BYTES)
        value = strict_json_loads(raw)
    except (OSError, UnicodeError, ValueError) as error:
        raise HostTaskExecutorError(f"{label} is not strict JSON") from error
    if not isinstance(value, dict):
        _fail(f"{label} must be an object")
    return value


def _parent_chain_has_symlink(path: Path) -> bool:
    current = Path(path.anchor)
    for part in path.parts[1:-1]:
        current /= part
        try:
            if stat.S_ISLNK(current.lstat().st_mode):
                return True
        except OSError:
            return True
    return False


def _external_directory(
    path: Path | str,
    *,
    repository: Path,
    label: str,
) -> Path:
    selected = Path(path)
    if not selected.is_absolute():
        _fail(f"{label} must be absolute")
    try:
        details = selected.lstat()
        resolved = selected.resolve(strict=True)
    except (OSError, RuntimeError, ValueError) as error:
        raise HostTaskExecutorError(f"{label} is unavailable") from error
    if (
        selected.is_symlink()
        or _parent_chain_has_symlink(selected)
        or not stat.S_ISDIR(details.st_mode)
    ):
        _fail(f"{label} must be a regular directory")
    if os.name != "nt" and (
        not hasattr(os, "geteuid")
        or details.st_uid != os.geteuid()
        or stat.S_IMODE(details.st_mode) & 0o077
    ):
        _fail(f"{label} must be owner-only")
    try:
        resolved.relative_to(repository.resolve(strict=True))
    except ValueError:
        return resolved
    _fail(f"{label} must be repository-external")


def _acl_report_is_verified(
    report: object,
    *,
    schema_version: str,
    recursive: bool,
) -> bool:
    if not isinstance(report, Mapping):
        return False
    checked = report.get("files_and_directories_checked")
    return bool(
        report.get("schema_version") == schema_version
        and report.get("platform") == "nt"
        and report.get("status") == "verified"
        and report.get("permissions_verified") is True
        and report.get("scan_complete") is True
        and type(checked) is int
        and checked >= 1
        and (recursive or checked == 1)
    )


def _verify_windows_acl(
    path: Path,
    *,
    recursive: bool,
    label: str,
) -> None:
    if os.name != "nt":
        return
    try:
        from deeplaw.windows_acl import (
            WINDOWS_ACL_SCHEMA,
            native_windows_acl_report,
            native_windows_path_acl_report,
        )

        report = (
            native_windows_acl_report(path)
            if recursive
            else native_windows_path_acl_report(path)
        )
    except Exception as error:
        raise HostTaskExecutorError(
            f"{label} native Windows ACL verification failed"
        ) from error
    if not _acl_report_is_verified(
        report,
        schema_version=WINDOWS_ACL_SCHEMA,
        recursive=recursive,
    ):
        raise HostTaskExecutorError(f"{label} native Windows ACL verification failed")


def _harden_windows_staging(path: Path, *, label: str) -> None:
    if os.name != "nt":
        return
    try:
        from deeplaw.windows_acl import WINDOWS_ACL_SCHEMA, harden_windows_vault

        result = harden_windows_vault(path)
    except Exception as error:
        raise HostTaskExecutorError(f"{label} native Windows ACL hardening failed") from error
    if (
        not isinstance(result, Mapping)
        or result.get("schema_version") != "deeplaw.windows-acl-hardening/v1"
        or result.get("platform") != "nt"
        or result.get("applied") is not True
        or type(result.get("item_count")) is not int
        or result["item_count"] < 1
        or not _acl_report_is_verified(
            result.get("verification"),
            schema_version=WINDOWS_ACL_SCHEMA,
            recursive=True,
        )
    ):
        raise HostTaskExecutorError(f"{label} native Windows ACL hardening failed")


def _output_target(path: Path | str, *, repository: Path) -> tuple[Path, Path]:
    selected = Path(path)
    if not selected.is_absolute():
        _fail("output root must be absolute")
    if selected.exists() or selected.is_symlink():
        _fail("output root must not already exist")
    parent = _external_directory(
        selected.parent,
        repository=repository,
        label="output parent",
    )
    _verify_windows_acl(parent, recursive=False, label="output parent")
    return parent / selected.name, parent


def _read_stable_file(path: Path, *, maximum_bytes: int) -> bytes:
    try:
        before = path.lstat()
    except OSError as error:
        raise HostTaskExecutorError("Host task staging file is unavailable") from error
    if (
        stat.S_ISLNK(before.st_mode)
        or not stat.S_ISREG(before.st_mode)
        or before.st_nlink != 1
        or not 0 <= before.st_size <= maximum_bytes
    ):
        _fail("Host task staging contains an unsafe file")
    flags = os.O_RDONLY | getattr(os, "O_BINARY", 0) | getattr(os, "O_NOFOLLOW", 0)
    if hasattr(os, "O_CLOEXEC"):
        flags |= os.O_CLOEXEC
    descriptor = -1
    chunks: list[bytes] = []
    total = 0
    try:
        descriptor = os.open(path, flags)
        fd_before = os.fstat(descriptor)
        if not host_preflight_receipt.portable_file_stat_matches(fd_before, before):
            _fail("Host task staging changed before it was read")
        while total <= maximum_bytes:
            chunk = os.read(descriptor, min(1024 * 1024, maximum_bytes + 1 - total))
            if not chunk:
                break
            chunks.append(chunk)
            total += len(chunk)
        fd_after = os.fstat(descriptor)
        after = path.lstat()
    except HostTaskExecutorError:
        raise
    except OSError as error:
        raise HostTaskExecutorError("Host task staging file is unavailable") from error
    finally:
        if descriptor >= 0:
            os.close(descriptor)
    raw = b"".join(chunks)
    if (
        len(raw) != before.st_size
        or len(raw) > maximum_bytes
        or host_preflight_receipt.stat_mutation_signature(fd_before)
        != host_preflight_receipt.stat_mutation_signature(fd_after)
        or host_preflight_receipt.stat_mutation_signature(before)
        != host_preflight_receipt.stat_mutation_signature(after)
        or not host_preflight_receipt.portable_file_stat_matches(fd_after, after)
    ):
        _fail("Host task staging changed while it was read")
    return raw


def _snapshot(root: Path) -> dict[str, bytes]:
    result: dict[str, bytes] = {}
    total = 0
    try:
        paths = sorted(root.rglob("*"))
        for path in paths:
            relative = path.relative_to(root).as_posix()
            if any(_FORBIDDEN_NAME.search(part) for part in PurePosixPath(relative).parts):
                _fail("Host task staging contains a forbidden path component")
            details = path.lstat()
            if stat.S_ISLNK(details.st_mode):
                _fail("Host task staging contains a symlink")
            if stat.S_ISDIR(details.st_mode):
                continue
            raw = _read_stable_file(path, maximum_bytes=MAX_FILE_BYTES)
            total += len(raw)
            if total > MAX_TOTAL_BYTES:
                _fail("Host task staging exceeds the total byte bound")
            result[relative] = raw
    except HostTaskExecutorError:
        raise
    except OSError as error:
        raise HostTaskExecutorError("Host task staging inventory is unavailable") from error
    return result


def _inventory(root: Path) -> dict[str, tuple[int, str]]:
    return {
        relative: (len(raw), _sha256(raw))
        for relative, raw in _snapshot(root).items()
    }


def _write_snapshot(snapshot: Mapping[str, bytes], target: Path) -> None:
    for relative, raw in sorted(snapshot.items()):
        path = target.joinpath(*PurePosixPath(relative).parts)
        try:
            path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
            if os.name != "nt":
                path.parent.chmod(0o700)
            flags = (
                os.O_WRONLY
                | os.O_CREAT
                | os.O_EXCL
                | getattr(os, "O_BINARY", 0)
                | getattr(os, "O_NOFOLLOW", 0)
            )
            if hasattr(os, "O_CLOEXEC"):
                flags |= os.O_CLOEXEC
            descriptor = os.open(path, flags, 0o600)
            try:
                offset = 0
                while offset < len(raw):
                    offset += os.write(descriptor, raw[offset:])
                os.fsync(descriptor)
            finally:
                os.close(descriptor)
        except OSError as error:
            raise HostTaskExecutorError("Host task snapshot copy failed") from error


def _fsync_directory(path: Path) -> None:
    if os.name == "nt":
        return
    descriptor = -1
    try:
        descriptor = os.open(
            path,
            os.O_RDONLY
            | getattr(os, "O_DIRECTORY", 0)
            | getattr(os, "O_CLOEXEC", 0),
        )
        os.fsync(descriptor)
    except OSError as error:
        raise HostTaskExecutorError("Host task directory durability check failed") from error
    finally:
        if descriptor >= 0:
            os.close(descriptor)


def _fsync_promoted_parent(descriptor: int) -> None:
    os.fsync(descriptor)


def _promote_no_replace(source: Path, target: Path) -> None:
    """Atomically rename one sibling directory without replacing a target."""

    if source.parent != target.parent:
        _fail("Host task atomic promotion requires sibling directories")
    directory = -1
    try:
        if os.name == "nt":
            os.rename(source, target)
            return
        parent_before = source.parent.lstat()
        directory = os.open(
            source.parent,
            os.O_RDONLY
            | getattr(os, "O_DIRECTORY", 0)
            | getattr(os, "O_NOFOLLOW", 0)
            | getattr(os, "O_CLOEXEC", 0),
        )
        if _stat_signature(os.fstat(directory)) != _stat_signature(parent_before):
            _fail("Host task output parent changed before promotion")
        libc = ctypes.CDLL(None, use_errno=True)
        if sys.platform == "darwin":
            rename = libc.renameatx_np
            no_replace = 0x00000004  # RENAME_EXCL
        elif sys.platform.startswith("linux"):
            rename = libc.renameat2
            no_replace = 0x1  # RENAME_NOREPLACE
        else:
            _fail("atomic no-replace promotion is unavailable")
        rename.argtypes = [
            ctypes.c_int,
            ctypes.c_char_p,
            ctypes.c_int,
            ctypes.c_char_p,
            ctypes.c_uint,
        ]
        rename.restype = ctypes.c_int
        result = rename(
            directory,
            os.fsencode(source.name),
            directory,
            os.fsencode(target.name),
            no_replace,
        )
        code = ctypes.get_errno()
        if result == 0:
            try:
                _fsync_promoted_parent(directory)
            except OSError as error:
                raise HostTaskPromotionCommittedError(
                    "Host task output committed without durability confirmation"
                ) from error
            return
        if code in {errno.EEXIST, errno.ENOTEMPTY}:
            _fail("Host task output appeared during admission")
        raise OSError(code, "atomic no-replace rename failed")
    except HostTaskExecutorError:
        raise
    except (AttributeError, OSError) as error:
        raise HostTaskExecutorError("Host task atomic promotion failed") from error
    finally:
        if directory >= 0:
            os.close(directory)


def _child_names(path: Path, *, label: str) -> set[str]:
    try:
        return {item.name for item in path.iterdir()}
    except OSError as error:
        raise HostTaskExecutorError(f"{label} is unavailable") from error


def _validate_topology(root: Path) -> None:
    if _child_names(root, label="Host task staging root") != {
        "slots",
        "receipts",
        "retained-broker-source",
        "candidate-inventory",
    }:
        _fail("Host task staging top-level inventory is not closed")
    if _child_names(
        root / "candidate-inventory",
        label="candidate inventory",
    ) != {"host-execution-identity.json"}:
        _fail("Host task candidate inventory is not closed")
    if _child_names(
        root / "retained-broker-source",
        label="retained broker sources",
    ) != {f"{host}.launcher-source" for host in HOSTS}:
        _fail("Host task broker source inventory is not closed")
    for family in ("slots", "receipts"):
        family_root = root / family
        if _child_names(family_root, label=f"Host task {family}") != set(HOSTS):
            _fail(f"Host task {family} Host inventory is not closed")
        for host in HOSTS:
            host_root = family_root / host
            if _child_names(host_root, label=f"Host task {family} Host") != set(
                TASK_CASES
            ):
                _fail(f"Host task {family} task inventory is not closed")
            if family == "receipts":
                for task in TASK_CASES:
                    if _child_names(
                        host_root / task,
                        label="Host task receipts slot",
                    ) != {"host-preflight.json", "host-process.json"}:
                        _fail("Host task receipt slot inventory is not closed")


def _validate_slot_topology(slot: Path, envelope: Mapping[str, Any]) -> None:
    payload = envelope.get("payload")
    if not isinstance(payload, Mapping):
        _fail("Host task typed manifest payload is unavailable")
    expected_files = {"host-event-sequence.json"}
    for source_key in TYPED_SOURCE_SLOTS:
        reference = payload.get(source_key)
        if not isinstance(reference, Mapping):
            _fail("Host task typed manifest source reference is unavailable")
        relative = reference.get("relative_path")
        if (
            not isinstance(relative, str)
            or not relative
            or "\\" in relative
            or PurePosixPath(relative).is_absolute()
            or any(part in {"", ".", ".."} for part in PurePosixPath(relative).parts)
        ):
            _fail("Host task typed manifest source path is unsafe")
        expected_files.add(PurePosixPath(relative).as_posix())
    actual_files: set[str] = set()
    actual_directories: set[str] = set()
    try:
        for path in slot.rglob("*"):
            relative = path.relative_to(slot).as_posix()
            details = path.lstat()
            if stat.S_ISLNK(details.st_mode):
                _fail("Host task slot contains a symlink")
            if stat.S_ISDIR(details.st_mode):
                actual_directories.add(relative)
            else:
                actual_files.add(relative)
    except HostTaskExecutorError:
        raise
    except OSError as error:
        raise HostTaskExecutorError("Host task slot topology is unavailable") from error
    expected_directories = {
        parent.as_posix()
        for relative in expected_files
        for parent in PurePosixPath(relative).parents
        if parent.as_posix() != "."
    }
    if actual_files != expected_files or actual_directories != expected_directories:
        _fail("Host task slot inventory is not closed")


def _digest(value: Any, *, label: str) -> str:
    if not isinstance(value, str) or _SHA256.fullmatch(value) is None:
        _fail(f"{label} must be a SHA-256 digest")
    return value


def _validate_execution_identity(
    value: Mapping[str, Any],
    *,
    identity: Mapping[str, Any],
) -> None:
    if set(value) != {"schema_version", "hosts"} or value.get(
        "schema_version"
    ) != EXECUTION_IDENTITY_SCHEMA:
        _fail("Host execution identity is not closed")
    hosts = value.get("hosts")
    if not isinstance(hosts, Mapping) or set(hosts) != set(HOSTS):
        _fail("Host execution identity does not cover both Hosts")
    source_sha = _digest(identity.get("source_sha256"), label="Host identity source")
    for host in HOSTS:
        row = hosts.get(host)
        expected_keys = {
            "selector_source_symlink",
            "execution_target_regular",
            "execution_target_single_link",
            "host_identity_sha256",
            "host_identity_source_sha256",
        }
        if not isinstance(row, Mapping) or set(row) != expected_keys:
            _fail("Host execution identity row is not closed")
        if (
            not isinstance(row["selector_source_symlink"], bool)
            or row["execution_target_regular"] is not True
            or row["execution_target_single_link"] is not True
            or row["host_identity_source_sha256"] != source_sha
            or row["host_identity_sha256"]
            != host_preflight_receipt.host_identity_sha256(identity["hosts"][host])
        ):
            _fail("Host execution identity differs from the frozen identity")


def _broker_sources(
    snapshot: Mapping[str, bytes],
    *,
    expected: Mapping[str, str],
) -> dict[str, str]:
    observed: dict[str, str] = {}
    for host in HOSTS:
        expected_sha = _digest(expected.get(host), label=f"{host} broker source")
        relative = f"retained-broker-source/{host}.launcher-source"
        raw = snapshot.get(relative)
        if (
            not isinstance(raw, bytes)
            or not 1 <= len(raw) <= BROKER_SOURCE_MAX_BYTES
            or _sha256(raw) != expected_sha
            or b"\x00" in raw
            or _BROKER_SECRET_LITERAL.search(raw) is not None
        ):
            _fail("retained broker source differs from the exact input")
        try:
            raw.decode("utf-8", errors="strict")
        except UnicodeDecodeError as error:
            raise HostTaskExecutorError(
                "retained broker source is not UTF-8 source text"
            ) from error
        observed[host] = expected_sha
    return observed


def _validate_preflight(
    value: Mapping[str, Any],
    *,
    host: str,
    identity: Mapping[str, Any],
    broker_sha256: str,
) -> None:
    try:
        admitted = host_preflight_receipt.validate_receipt(value)
        binary = host_preflight_receipt.host_binary_identity(identity, host)
    except (TypeError, ValueError) as error:
        raise HostTaskExecutorError("Host preflight receipt was rejected") from error
    host_row = admitted.get("host")
    broker = admitted.get("broker_source")
    if (
        admitted.get("status") != "passed"
        or not isinstance(host_row, Mapping)
        or host_row.get("name") != host
        or host_row.get("version") != binary["version"]
        or host_row.get("sha256") != binary["sha256"]
        or not isinstance(broker, Mapping)
        or broker.get("repository_external") is not True
        or broker.get("owner_only_mode") is not True
        or broker.get("sha256") != broker_sha256
        or broker.get("expected_sha256") != broker_sha256
    ):
        _fail("Host preflight receipt differs from the exact Host or broker")


def _validate_staging(
    root: Path,
    *,
    candidate: Mapping[str, Any],
    run_binding: Mapping[str, int],
    host_identity_input: Path | str,
    identity: Mapping[str, Any],
    broker_sha256s: Mapping[str, str],
) -> list[dict[str, Any]]:
    snapshot = _snapshot(root)
    _validate_topology(root)
    execution_identity = _strict_object(
        root / "candidate-inventory" / "host-execution-identity.json",
        label="Host execution identity",
    )
    _validate_execution_identity(execution_identity, identity=identity)
    retained_brokers = _broker_sources(snapshot, expected=broker_sha256s)
    results: list[dict[str, Any]] = []
    seen_nonces: set[str] = set()
    for host in HOSTS:
        expected_host_binary = host_preflight_receipt.host_binary_identity(identity, host)
        expected_identity_sha = host_preflight_receipt.host_identity_sha256(
            identity["hosts"][host]
        )
        for task in TASK_CASES:
            slot = root / "slots" / host / task
            manifest = slot / "host-event-sequence.json"
            envelope = _strict_object(manifest, label="Host task typed manifest")
            corpus = envelope.get("corpus")
            if (
                envelope.get("schema_version") != TYPED_SCHEMA
                or envelope.get("kind") != "host_event_sequence"
                or not isinstance(corpus, Mapping)
                or corpus.get("role") != "host_qualification"
            ):
                _fail("Host task typed manifest has the wrong kind or corpus")
            _validate_slot_topology(slot, envelope)
            corpus_sha = _digest(corpus.get("sha256"), label="Host task corpus")
            record_sha = _digest(
                envelope.get("record_sha256"),
                label="Host task typed manifest record",
            )
            try:
                derived = validate_retained_manifest(
                    manifest,
                    root=slot,
                    expected_candidate=candidate,
                    expected_workflow_run_id=run_binding["evidence_run_id"],
                    expected_corpus_sha256=corpus_sha,
                    host_identity_input=host_identity_input,
                )
            except (OSError, ValueError, HostTaskQualificationError) as error:
                raise HostTaskExecutorError("Host task typed evidence was rejected") from error
            metrics = derived.get("metrics")
            if (
                derived.get("status") != "passed"
                or derived.get("evidence_record_sha256") != record_sha
                or not isinstance(metrics, Mapping)
                or metrics.get("host") != host
                or metrics.get("task_case") != task
                or not isinstance(metrics.get("run_id"), str)
            ):
                _fail("Host task derived metrics differ from the frozen slot")
            preflight = _strict_object(
                root / "receipts" / host / task / "host-preflight.json",
                label="Host preflight receipt",
            )
            _validate_preflight(
                preflight,
                host=host,
                identity=identity,
                broker_sha256=retained_brokers[host],
            )
            process = _strict_object(
                root / "receipts" / host / task / "host-process.json",
                label="Host process receipt",
            )
            try:
                admitted_process = host_process_receipt_v2.validate_receipt(
                    process,
                    expected_host=host,
                    expected_task_case=task,
                    expected_run_id=metrics["run_id"],
                    expected_candidate=candidate,
                    expected_run_binding={
                        "evidence_run_id": run_binding["evidence_run_id"],
                        "qualification_run_id": run_binding["qualification_run_id"],
                    },
                    expected_broker_sha256=retained_brokers[host],
                    expected_host_identity_sha256=expected_identity_sha,
                    expected_host_identity_source_sha256=identity["source_sha256"],
                    expected_host_binary=expected_host_binary,
                    seen_nonce_sha256s=seen_nonces,
                )
            except (TypeError, ValueError) as error:
                raise HostTaskExecutorError("Host process receipt was rejected") from error
            topology_fields = (
                "selector_source_symlink",
                "execution_target_regular",
                "execution_target_single_link",
            )
            execution_row = execution_identity["hosts"][host]
            if any(
                admitted_process.get(field) != execution_row[field]
                for field in topology_fields
            ):
                _fail("Host process receipt differs from the execution topology")
            native = admitted_process.get("native_event_binding")
            expected_native = {
                "event_sequence_sha256": metrics.get("event_sequence_sha256"),
                "session_identity_sha256": metrics.get("session_identity_sha256"),
                "lifecycle_record_sha256": metrics.get("lifecycle_record_sha256"),
            }
            if native != expected_native:
                _fail("Host process receipt differs from the typed native event binding")
            results.append(dict(derived))
    try:
        validate_host_task_matrix(results, host_identity_input=host_identity_input)
    except (TypeError, ValueError, HostTaskQualificationError) as error:
        raise HostTaskExecutorError("Host task matrix was rejected") from error
    return results


def admit_host_task_staging(
    source_root: Path | str,
    output_root: Path | str,
    *,
    handoff: Path | str,
    candidate_binding_input: Path | str,
    evidence_run_id: int,
    qualification_run_id: int,
    host_identity_input: Path | str,
    codex_broker_sha256: str,
    opencode_broker_sha256: str,
    candidate_wheel: Path | str | None = None,
    repository: Path = REPOSITORY,
) -> dict[str, Any]:
    """Validate and transactionally promote one complete external Host tree."""

    if any(
        type(value) is not int or value < 1
        for value in (evidence_run_id, qualification_run_id)
    ):
        _fail("Host task run binding is invalid")
    source = _external_directory(
        source_root,
        repository=repository,
        label="Host task source root",
    )
    _verify_windows_acl(
        source,
        recursive=True,
        label="Host task source root",
    )
    output, output_parent = _output_target(output_root, repository=repository)
    if source == output_parent or source in output_parent.parents:
        _fail("Host task source and output roots overlap")
    try:
        identity, identity_raw = (
            host_preflight_receipt.load_host_identity_input_with_bytes(
                host_identity_input,
                repository=repository,
            )
        )
        validate_external_collector_handoff(
            handoff,
            host_identity_input=host_identity_input,
        )
        candidate = load_exact_candidate_binding(
            candidate_binding_input,
            candidate_wheel=candidate_wheel,
            repository=repository,
        )
        identity_after, identity_raw_after = (
            host_preflight_receipt.load_host_identity_input_with_bytes(
                host_identity_input,
                repository=repository,
            )
        )
    except (OSError, ValueError, HostTaskQualificationError) as error:
        raise HostTaskExecutorError("Host task control binding was rejected") from error
    if identity != identity_after or identity_raw != identity_raw_after:
        _fail("Host identity changed during control binding")
    run_binding = {
        "evidence_run_id": evidence_run_id,
        "qualification_run_id": qualification_run_id,
    }
    broker_sha256s = {
        "codex": _digest(codex_broker_sha256, label="Codex broker source"),
        "opencode": _digest(opencode_broker_sha256, label="OpenCode broker source"),
    }
    _validate_staging(
        source,
        candidate=candidate,
        run_binding=run_binding,
        host_identity_input=host_identity_input,
        identity=identity,
        broker_sha256s=broker_sha256s,
    )
    try:
        identity_final, identity_raw_final = (
            host_preflight_receipt.load_host_identity_input_with_bytes(
                host_identity_input,
                repository=repository,
            )
        )
    except (OSError, ValueError) as error:
        raise HostTaskExecutorError("Host identity final binding was rejected") from error
    if identity_final != identity or identity_raw_final != identity_raw:
        _fail("Host identity changed during staging validation")
    source_snapshot = _snapshot(source)
    try:
        staging = Path(
            tempfile.mkdtemp(prefix=f".{output.name}.admit-", dir=output_parent)
        )
    except OSError as error:
        raise HostTaskExecutorError("Host task temporary staging creation failed") from error
    promoted = False
    try:
        try:
            if os.name != "nt":
                staging.chmod(0o700)
        except OSError as error:
            raise HostTaskExecutorError(
                "Host task temporary staging hardening failed"
            ) from error
        _harden_windows_staging(
            staging,
            label="Host task temporary staging",
        )
        _write_snapshot(source_snapshot, staging)
        if _inventory(staging) != {
            relative: (len(raw), _sha256(raw))
            for relative, raw in source_snapshot.items()
        }:
            _fail("Host task staged bytes differ from the external source")
        _harden_windows_staging(
            staging,
            label="Host task temporary staging",
        )
        _verify_windows_acl(
            staging,
            recursive=True,
            label="Host task temporary staging",
        )
        _validate_staging(
            staging,
            candidate=candidate,
            run_binding=run_binding,
            host_identity_input=host_identity_input,
            identity=identity,
            broker_sha256s=broker_sha256s,
        )
        try:
            identity_promote, identity_raw_promote = (
                host_preflight_receipt.load_host_identity_input_with_bytes(
                    host_identity_input,
                    repository=repository,
                )
            )
        except (OSError, ValueError) as error:
            raise HostTaskExecutorError(
                "Host identity promotion binding was rejected"
            ) from error
        if identity_promote != identity or identity_raw_promote != identity_raw:
            _fail("Host identity changed during staged-byte validation")
        _fsync_directory(staging)
        try:
            _promote_no_replace(staging, output)
        except HostTaskPromotionCommittedError:
            promoted = True
            raise
        else:
            promoted = True
    finally:
        if not promoted:
            try:
                shutil.rmtree(staging)
            except OSError as error:
                raise HostTaskExecutorError(
                    "Host task staging rollback cleanup failed"
                ) from error
            if staging.exists():
                _fail("Host task staging rollback cleanup was not confirmed")
    return {
        "schema_version": "deeplaw.v013-host-task-executor-admission/v1",
        "status": "admitted",
        "slot_count": len(HOSTS) * len(TASK_CASES),
        "host_count": len(HOSTS),
        "task_count_per_host": len(TASK_CASES),
        "candidate_binding": dict(candidate),
        "evidence_run_id": evidence_run_id,
        "qualification_run_id": qualification_run_id,
        "claim_eligible": False,
        "formal_admission": False,
        "release_ready": False,
    }


def _main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-root", type=Path, required=True)
    parser.add_argument("--output-root", type=Path, required=True)
    parser.add_argument("--handoff", type=Path, required=True)
    parser.add_argument("--candidate-binding-input", type=Path, required=True)
    parser.add_argument("--candidate-wheel", type=Path)
    parser.add_argument("--evidence-run-id", type=int, required=True)
    parser.add_argument("--qualification-run-id", type=int, required=True)
    parser.add_argument("--host-identity-input", type=Path, required=True)
    parser.add_argument("--codex-broker-sha256", required=True)
    parser.add_argument("--opencode-broker-sha256", required=True)
    args = parser.parse_args(argv)
    result = admit_host_task_staging(
        args.source_root,
        args.output_root,
        handoff=args.handoff,
        candidate_binding_input=args.candidate_binding_input,
        candidate_wheel=args.candidate_wheel,
        evidence_run_id=args.evidence_run_id,
        qualification_run_id=args.qualification_run_id,
        host_identity_input=args.host_identity_input,
        codex_broker_sha256=args.codex_broker_sha256,
        opencode_broker_sha256=args.opencode_broker_sha256,
    )
    print(json.dumps(result, sort_keys=True, separators=(",", ":")))
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(_main())
    except (HostTaskExecutorError, OSError) as error:
        print(f"Host task executor failed: {type(error).__name__}", file=sys.stderr)
        raise SystemExit(2) from error
