from __future__ import annotations

import json
import os
import secrets
import shutil
import sqlite3
from contextlib import suppress
from pathlib import Path, PurePosixPath
from typing import Any

from .host_runtime import host_product_readiness
from .knowledge_inbox import list_inbox_artifacts, verify_inbox_artifact
from .knowledge_jobs import list_ingest_jobs
from .knowledge_store import (
    KnowledgeVault,
    _copy_vault_payload,
    knowledge_vault_permission_report,
)
from .source_adapters import validate_source_ir_database
from .util import canonical_json, sha256_bytes, sha256_file, strict_json_loads

KNOWLEDGE_SNAPSHOT_SCHEMA = "deeplaw.knowledge-snapshot/v1"
KNOWLEDGE_DOCTOR_SCHEMA = "deeplaw.knowledge-doctor/v3"

_MAX_SNAPSHOT_FILES = 300_000
_MAX_MANIFEST_BYTES = 64 * 1024 * 1024
_MAX_SCHEMA_PEEK_BYTES = 128 * 1024 * 1024
_MAX_SIDECAR_FILE_BYTES = 512 * 1024 * 1024
_PRESERVED_SIDECARS = (
    PurePosixPath("inbox"),
    PurePosixPath("operations"),
    PurePosixPath("derived/retrieval-profiles"),
)


def _owner_directory(path: Path) -> Path:
    if path.is_symlink() or (path.exists() and not path.is_dir()):
        raise RuntimeError("maintenance directory is unsafe")
    path.mkdir(parents=True, exist_ok=True, mode=0o700)
    if os.name != "nt":
        os.chmod(path, 0o700)
    return path


def _bounded_sorted_entries(root: Path, *, message: str) -> list[Path]:
    entries: list[Path] = []
    for path in root.rglob("*"):
        entries.append(path)
        if len(entries) > _MAX_SNAPSHOT_FILES:
            raise ValueError(message)
    return sorted(entries, key=lambda item: item.as_posix())


def _snapshot_schema(manifest_path: Path) -> str | None:
    if manifest_path.is_symlink() or not manifest_path.is_file():
        return None
    try:
        if not 1 <= manifest_path.stat().st_size <= _MAX_SCHEMA_PEEK_BYTES:
            return None
        candidate = strict_json_loads(manifest_path.read_bytes())
    except (OSError, ValueError):
        return None
    return candidate.get("schema_version") if isinstance(candidate, dict) else None


def _write_json(path: Path, value: dict[str, Any]) -> None:
    if path.is_symlink() or (path.exists() and not path.is_file()):
        raise RuntimeError("maintenance manifest path is unsafe")
    payload = (json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n").encode()
    if not 1 <= len(payload) <= _MAX_MANIFEST_BYTES:
        raise ValueError("maintenance manifest exceeds its size bound")
    temporary = path.with_name(f".{path.name}.{secrets.token_hex(8)}.tmp")
    descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(payload)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
        if os.name != "nt":
            os.chmod(path, 0o600)
    except BaseException:
        temporary.unlink(missing_ok=True)
        raise


def _safe_copy_tree(source: Path, destination: Path) -> None:
    if source.is_symlink() or not source.is_dir():
        raise RuntimeError("snapshot sidecar source is unsafe")
    _owner_directory(destination)
    count = 0
    for path in _bounded_sorted_entries(
        source,
        message="snapshot sidecar inventory exceeds its file bound",
    ):
        count += 1
        if path.is_symlink():
            raise RuntimeError("snapshot sidecar contains a symbolic link")
        relative = path.relative_to(source)
        target = destination / relative
        if path.is_dir():
            _owner_directory(target)
            continue
        if not path.is_file() or path.stat().st_size > _MAX_SIDECAR_FILE_BYTES:
            raise RuntimeError("snapshot sidecar contains an unsafe or oversized entry")
        _owner_directory(target.parent)
        shutil.copyfile(path, target)
        if os.name != "nt":
            os.chmod(target, 0o600)


def _snapshot_inventory(root: Path) -> list[dict[str, Any]]:
    inventory: list[dict[str, Any]] = []
    for path in _bounded_sorted_entries(
        root,
        message="snapshot inventory exceeds its file bound",
    ):
        if path.is_symlink():
            raise RuntimeError("snapshot contains a symbolic link")
        if not path.is_file():
            continue
        relative = path.relative_to(root).as_posix()
        inventory.append(
            {
                "path": relative,
                "byte_size": path.stat().st_size,
                "sha256": sha256_file(path),
            }
        )
    return inventory


def create_knowledge_snapshot(
    vault: KnowledgeVault,
    output: str | Path,
    *,
    include_operator_state: bool = True,
) -> dict[str, Any]:
    from .knowledge_autonomy import autonomous_core_installed, create_autonomous_snapshot

    if autonomous_core_installed(vault.root):
        return create_autonomous_snapshot(
            vault.root,
            output,
            include_operator_state=include_operator_state,
        )
    if not vault.verify_integrity()["valid"]:
        raise RuntimeError("snapshot requires a healthy canonical knowledge vault")
    source_integrity = vault.verify_source_files(
        source["source_id"] for source in vault.all_sources()
    )
    if not source_integrity["valid"]:
        raise RuntimeError("snapshot requires intact stored source files")
    destination = Path(output).expanduser().absolute()
    if destination.is_symlink() or destination.exists():
        raise FileExistsError("snapshot output must be a new non-symlink directory")
    destination.parent.mkdir(parents=True, exist_ok=True)
    stage = destination.parent / f".{destination.name}.{secrets.token_hex(8)}.tmp"
    _owner_directory(stage)
    snapshot_vault = stage / "vault"
    try:
        _copy_vault_payload(vault.root, snapshot_vault)
        if include_operator_state:
            for relative in _PRESERVED_SIDECARS:
                source = vault.root.joinpath(*relative.parts)
                if source.exists():
                    _safe_copy_tree(
                        source,
                        snapshot_vault.joinpath(*relative.parts),
                    )
        with KnowledgeVault(snapshot_vault, read_only=True) as copied:
            integrity = copied.verify_integrity()
            if (
                not integrity["valid"]
                or copied.vault_id != vault.vault_id
                or copied.revision != vault.revision
                or copied.audit_head != vault.audit_head
            ):
                raise RuntimeError("snapshot copy does not match the pinned vault state")
        inventory = _snapshot_inventory(snapshot_vault)
        inventory_sha256 = sha256_bytes(canonical_json(inventory).encode())
        body = {
            "schema_version": KNOWLEDGE_SNAPSHOT_SCHEMA,
            "vault_id": vault.vault_id,
            "vault_revision": vault.revision,
            "audit_head": vault.audit_head,
            "created_at": vault.latest_event_at(),
            "include_operator_state": include_operator_state,
            "file_count": len(inventory),
            "inventory": inventory,
            "inventory_sha256": inventory_sha256,
        }
        manifest = {
            **body,
            "snapshot_sha256": sha256_bytes(canonical_json(body).encode()),
        }
        _write_json(stage / "snapshot.json", manifest)
        os.replace(stage, destination)
    except BaseException:
        if stage.exists():
            shutil.rmtree(stage)
        raise
    verification = verify_knowledge_snapshot(destination)
    if not verification["valid"]:
        raise RuntimeError("created snapshot failed self-verification")
    return {
        **manifest,
        "path": str(destination),
        "valid": True,
    }


def verify_knowledge_snapshot(
    snapshot: str | Path,
    *,
    expected_vault_id: str | None = None,
) -> dict[str, Any]:
    root = Path(snapshot).expanduser().absolute()
    manifest_path = root / "snapshot.json"
    copied_root = root / "vault"
    if _snapshot_schema(manifest_path) == "deeplaw.autonomous-snapshot/v1":
        from .knowledge_autonomy import verify_autonomous_snapshot

        return verify_autonomous_snapshot(
            root,
            expected_vault_id=expected_vault_id,
        )
    errors: list[str] = []
    manifest: dict[str, Any] = {}
    try:
        if root.is_symlink() or not root.is_dir():
            raise RuntimeError("snapshot root is missing or unsafe")
        if (
            manifest_path.is_symlink()
            or not manifest_path.is_file()
            or not 1 <= manifest_path.stat().st_size <= _MAX_MANIFEST_BYTES
        ):
            raise RuntimeError("snapshot manifest is missing or unsafe")
        value = strict_json_loads(manifest_path.read_bytes())
        if not isinstance(value, dict):
            raise RuntimeError("snapshot manifest must be an object")
        manifest = value
        expected_fields = {
            "schema_version",
            "vault_id",
            "vault_revision",
            "audit_head",
            "created_at",
            "include_operator_state",
            "file_count",
            "inventory",
            "inventory_sha256",
            "snapshot_sha256",
        }
        body = {key: value[key] for key in expected_fields if key != "snapshot_sha256"}
        if (
            set(value) != expected_fields
            or value["schema_version"] != KNOWLEDGE_SNAPSHOT_SCHEMA
            or (expected_vault_id is not None and value["vault_id"] != expected_vault_id)
            or not isinstance(value["inventory"], list)
            or value["file_count"] != len(value["inventory"])
            or value["file_count"] > _MAX_SNAPSHOT_FILES
            or value["inventory_sha256"]
            != sha256_bytes(canonical_json(value["inventory"]).encode())
            or value["snapshot_sha256"] != sha256_bytes(canonical_json(body).encode())
        ):
            raise RuntimeError("snapshot manifest contract or digest is invalid")
        actual = _snapshot_inventory(copied_root)
        if actual != value["inventory"]:
            raise RuntimeError("snapshot file inventory does not match its manifest")
        with KnowledgeVault(copied_root, read_only=True) as vault:
            if (
                vault.vault_id != value["vault_id"]
                or vault.revision != value["vault_revision"]
                or vault.audit_head != value["audit_head"]
                or not vault.verify_integrity()["valid"]
                or not vault.verify_source_files(
                    source["source_id"] for source in vault.all_sources()
                )["valid"]
            ):
                raise RuntimeError("snapshot canonical vault verification failed")
    except (
        KeyError,
        OSError,
        RuntimeError,
        sqlite3.DatabaseError,
        TypeError,
        ValueError,
    ) as error:
        errors.append(str(error))
    return {
        "schema_version": "deeplaw.knowledge-snapshot-verification/v1",
        "path": str(root),
        "vault_id": manifest.get("vault_id"),
        "vault_revision": manifest.get("vault_revision"),
        "errors": errors,
        "valid": not errors,
    }


def _copy_snapshot_payload(source: Path, destination: Path) -> None:
    if destination.exists() or destination.is_symlink():
        raise FileExistsError("snapshot restore staging directory already exists")
    _safe_copy_tree(source, destination)


def _remove_restore_candidate(path: Path) -> None:
    if path.is_symlink() or path.is_file():
        path.unlink()
    elif path.exists():
        shutil.rmtree(path)


def restore_knowledge_snapshot(
    destination: str | Path,
    *,
    snapshot: str | Path,
    confirm: bool,
) -> dict[str, Any]:
    snapshot_root = Path(snapshot).expanduser().absolute()
    manifest_path = snapshot_root / "snapshot.json"
    if _snapshot_schema(manifest_path) == "deeplaw.autonomous-snapshot/v1":
        from .knowledge_autonomy import restore_autonomous_snapshot

        return restore_autonomous_snapshot(
            destination,
            snapshot=snapshot_root,
            confirm=confirm,
        )
    if not confirm:
        raise ValueError("snapshot restore requires explicit confirmation")
    target = Path(destination).expanduser().absolute()
    verification = verify_knowledge_snapshot(snapshot_root)
    if not verification["valid"]:
        raise RuntimeError("snapshot restore requires a valid snapshot")
    vault_id = verification["vault_id"]
    if target.is_symlink() or (target.exists() and not target.is_dir()):
        raise RuntimeError("snapshot restore destination is unsafe")
    if target.exists():
        try:
            with KnowledgeVault(target, read_only=True) as current:
                current_vault_id = current.vault_id
        except (OSError, RuntimeError, sqlite3.DatabaseError):
            manifest_path = target / "vault.json"
            raw = strict_json_loads(manifest_path.read_bytes())
            current_vault_id = raw.get("vault_id") if isinstance(raw, dict) else None
        if current_vault_id != vault_id:
            raise RuntimeError("snapshot vault identity does not match the restore target")
    target.parent.mkdir(parents=True, exist_ok=True)
    token = secrets.token_hex(6)
    stage = target.with_name(f".{target.name}.snapshot-restore-{token}.tmp")
    retained = target.with_name(f"{target.name}.pre-restore-{token}")
    _copy_snapshot_payload(snapshot_root / "vault", stage)
    with KnowledgeVault(stage, read_only=True) as restored:
        if restored.vault_id != vault_id or not restored.verify_integrity()["valid"]:
            shutil.rmtree(stage)
            raise RuntimeError("restored snapshot failed pre-swap verification")
    previous_retained: str | None = None
    try:
        if target.exists():
            os.replace(target, retained)
            previous_retained = str(retained)
        try:
            os.replace(stage, target)
        except BaseException:
            if retained.exists():
                os.replace(retained, target)
            raise
        with KnowledgeVault(target, read_only=True) as restored:
            if not restored.verify_integrity()["valid"]:
                raise RuntimeError("restored snapshot failed post-swap verification")
            revision = restored.revision
            audit_head = restored.audit_head
    except BaseException:
        rollback_error: BaseException | None = None
        if retained.exists():
            failed = target.with_name(f".{target.name}.failed-restore-{token}.tmp")
            try:
                if failed.exists() or failed.is_symlink():
                    raise RuntimeError("snapshot restore rollback path already exists")
                if target.exists() or target.is_symlink():
                    os.replace(target, failed)
                os.replace(retained, target)
                with suppress(OSError):
                    _remove_restore_candidate(failed)
            except BaseException as error:
                rollback_error = error
        if stage.exists():
            shutil.rmtree(stage)
        if rollback_error is not None:
            raise RuntimeError(
                "snapshot restore failed and the retained vault could not be restored"
            ) from rollback_error
        raise
    return {
        "schema_version": "deeplaw.knowledge-snapshot-restore/v1",
        "vault_id": vault_id,
        "snapshot_path": str(snapshot_root),
        "destination": str(target),
        "retained_previous_vault": previous_retained,
        "revision": revision,
        "audit_head": audit_head,
        "restored": True,
        "valid": True,
    }


def detect_knowledge_orphans(vault: KnowledgeVault) -> dict[str, Any]:
    expected_source_files = {
        source["stored_name"]
        for source in vault.all_sources()
        if source["stored_name"] is not None
    }
    actual_source_files = {
        path.name
        for path in (vault.root / "sources").iterdir()
        if path.is_file() and not path.is_symlink()
    }
    unsafe_source_entries = sorted(
        path.name
        for path in (vault.root / "sources").iterdir()
        if path.is_symlink() or not path.is_file()
    )
    foreign_key_violations = [
        dict(row) for row in vault.connection.execute("PRAGMA foreign_key_check").fetchall()
    ]
    asset_ids = {
        row["asset_id"] for row in vault.connection.execute("SELECT asset_id FROM assets")
    }
    search_ids = {
        row["asset_id"] for row in vault.connection.execute("SELECT asset_id FROM asset_search")
    }
    temporary_files = sorted(
        path.relative_to(vault.root).as_posix()
        for path in vault.root.rglob("*")
        if path.is_file()
        and not path.is_symlink()
        and (path.name.endswith(".tmp") or path.name.startswith(".tmp-"))
    )
    return {
        "schema_version": "deeplaw.knowledge-orphan-report/v1",
        "vault_id": vault.vault_id,
        "untracked_source_files": sorted(actual_source_files - expected_source_files),
        "missing_source_files": sorted(expected_source_files - actual_source_files),
        "unsafe_source_entries": unsafe_source_entries,
        "asset_search_without_asset": sorted(search_ids - asset_ids),
        "assets_missing_search_row": sorted(asset_ids - search_ids),
        "foreign_key_violations": foreign_key_violations[:100],
        "foreign_key_violations_truncated": len(foreign_key_violations) > 100,
        "temporary_files": temporary_files[:1000],
        "temporary_files_truncated": len(temporary_files) > 1000,
        "valid": not (
            actual_source_files - expected_source_files
            or expected_source_files - actual_source_files
            or unsafe_source_entries
            or search_ids - asset_ids
            or asset_ids - search_ids
            or foreign_key_violations
        ),
    }


def garbage_collect_derived(
    vault: KnowledgeVault,
    *,
    confirm: bool,
    dry_run: bool = True,
) -> dict[str, Any]:
    orphans = detect_knowledge_orphans(vault)
    candidates = list(orphans["temporary_files"])
    if not dry_run and not confirm:
        raise ValueError("derived garbage collection requires explicit confirmation")
    removed: list[str] = []
    if not dry_run:
        for relative in candidates:
            pure = PurePosixPath(relative)
            if pure.is_absolute() or ".." in pure.parts:
                raise RuntimeError("garbage collection candidate path is unsafe")
            path = vault.root.joinpath(*pure.parts)
            if path.is_symlink() or not path.is_file():
                continue
            path.unlink()
            removed.append(relative)
    return {
        "schema_version": "deeplaw.knowledge-gc/v1",
        "vault_id": vault.vault_id,
        "dry_run": dry_run,
        "candidate_count": len(candidates),
        "candidates": candidates,
        "removed_count": len(removed),
        "removed": removed,
        "canonical_sources_removed": 0,
        "canonical_database_modified": False,
    }


def _orphans_allow_search_index_rebuild(report: dict[str, Any]) -> bool:
    return not any(
        report.get(field)
        for field in (
            "untracked_source_files",
            "missing_source_files",
            "unsafe_source_entries",
            "foreign_key_violations",
        )
    )


def knowledge_doctor(
    vault_path: str | Path,
    *,
    repair_derived: bool = False,
) -> dict[str, Any]:
    from .knowledge_autonomy import AutonomousKnowledgeStore, autonomous_core_installed

    permission_report = knowledge_vault_permission_report(vault_path)
    errors: list[str] = []
    checks: dict[str, Any] = {}
    repaired: dict[str, Any] | None = None
    try:
        with KnowledgeVault(vault_path, read_only=not repair_derived) as vault:
            integrity = vault.verify_integrity()
            quick_check = vault.connection.execute("PRAGMA quick_check").fetchone()[0]
            source_integrity = vault.verify_source_files(
                source["source_id"] for source in vault.all_sources()
            )
            source_ir = (
                validate_source_ir_database(vault.connection)
                if vault.identity_v2_enabled
                else {"valid": False, "reason": "identity_v2_not_installed"}
            )
            orphans = detect_knowledge_orphans(vault)
            if (vault.root / "operations").exists():
                try:
                    jobs = list_ingest_jobs(vault)
                    job_records_valid = True
                except (OSError, RuntimeError, ValueError) as error:
                    jobs = {"jobs": []}
                    job_records_valid = False
                    errors.append(f"jobs: {error}")
            else:
                jobs = {"jobs": []}
                job_records_valid = True
            try:
                inbox_lists = (
                    [
                        list_inbox_artifacts(vault, state=state, limit=500)
                        for state in ("pending", "processed", "rejected")
                    ]
                    if (vault.root / "inbox").exists()
                    else []
                )
                inbox_artifacts = [
                    item
                    for listing in inbox_lists
                    for item in listing["artifacts"]
                ]
                invalid_inbox = [
                    item["artifact_id"]
                    for item in inbox_artifacts
                    if not verify_inbox_artifact(vault, item["artifact_id"])["valid"]
                ]
                invalid_inbox.extend(
                    f"invalid-record:{state}"
                    for state, listing in zip(
                        ("pending", "processed", "rejected")[: len(inbox_lists)],
                        inbox_lists,
                        strict=True,
                    )
                    if listing["invalid_artifact_count"]
                )
                inbox = {"total": len(inbox_artifacts)}
            except (OSError, RuntimeError, ValueError) as error:
                inbox = {"total": 0, "artifacts": []}
                invalid_inbox = ["inbox-unreadable"]
                errors.append(f"inbox: {error}")
            checks = {
                "vault_id": vault.vault_id,
                "revision": vault.revision,
                "audit_head": vault.audit_head,
                "sqlite_quick_check": quick_check,
                "canonical_integrity": integrity,
                "source_integrity": source_integrity,
                "source_ir": source_ir,
                "orphans": orphans,
                "job_record_count": len(jobs["jobs"]),
                "job_records_valid": job_records_valid,
                "inbox_artifact_count": inbox.get("total", 0),
                "invalid_inbox_artifact_ids": invalid_inbox,
            }
            canonical_valid = bool(
                quick_check == "ok"
                and integrity["valid"]
                and source_integrity["valid"]
                and source_ir["valid"]
                and orphans["valid"]
            )
            if repair_derived:
                repair_preflight_valid = bool(
                    quick_check == "ok"
                    and vault.derived_indexes_rebuildable(integrity)
                    and source_integrity["valid"]
                    and source_ir["valid"]
                    and _orphans_allow_search_index_rebuild(orphans)
                )
                if not repair_preflight_valid:
                    raise RuntimeError(
                        "derived repair is blocked while canonical integrity is invalid"
                    )
                repaired = vault.rebuild_derived_indexes()
                integrity = vault.verify_integrity()
                orphans = detect_knowledge_orphans(vault)
                checks["canonical_integrity"] = integrity
                checks["orphans"] = orphans
                canonical_valid = bool(
                    quick_check == "ok"
                    and integrity["valid"]
                    and source_integrity["valid"]
                    and source_ir["valid"]
                    and orphans["valid"]
                )
        autonomous_installed = autonomous_core_installed(vault_path)
        checks["autonomous_core"] = {"installed": autonomous_installed}
        if autonomous_installed:
            with AutonomousKnowledgeStore(vault_path, read_only=True) as autonomous:
                autonomous_integrity = autonomous.verify()
            checks["autonomous_core"] = {
                "installed": True,
                "integrity": autonomous_integrity,
            }
            canonical_valid = bool(canonical_valid and autonomous_integrity["valid"])
            if repair_derived:
                if not canonical_valid:
                    raise RuntimeError(
                        "derived repair is blocked while autonomous canonical integrity is invalid"
                    )
                with AutonomousKnowledgeStore(vault_path, read_only=False) as autonomous:
                    autonomous_repair = autonomous.rebuild_derived()
                repaired = {
                    "legacy": repaired,
                    "autonomous": autonomous_repair,
                }
    except (OSError, RuntimeError, sqlite3.DatabaseError, ValueError) as error:
        errors.append(str(error))
        canonical_valid = False
    ready = bool(
        canonical_valid
        and permission_report["permissions_verified"]
        and checks.get("job_records_valid", False)
        and not checks.get("invalid_inbox_artifact_ids", [])
    )
    autonomous_vault_ready = bool(
        ready and checks.get("autonomous_core", {}).get("installed") is True
    )
    return {
        "schema_version": KNOWLEDGE_DOCTOR_SCHEMA,
        "path": str(Path(vault_path).expanduser().absolute()),
        "permissions": permission_report,
        "checks": checks,
        "repair": repaired,
        "errors": errors,
        "canonical_valid": canonical_valid,
        "ready": ready,
        "product_readiness": host_product_readiness(
            autonomous_vault_ready=autonomous_vault_ready
        ),
    }
