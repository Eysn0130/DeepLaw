from __future__ import annotations

import json
import os
import re
import secrets
import shutil
import sqlite3
import stat
from collections import OrderedDict
from collections.abc import Iterable
from contextlib import AbstractContextManager
from copy import deepcopy
from datetime import UTC, datetime, timedelta
from pathlib import Path
from threading import RLock
from typing import Any, Literal, cast

from .knowledge_identity import (
    canonical_origin_commitment,
    identity_snapshot,
    identity_tables_present,
    install_identity_tables,
    make_asset_revision_id,
    make_collection_id,
    make_knowledge_key,
    make_source_key,
    normalize_logical_path,
    record_governance_revision,
    record_lineage_transition,
    record_relation_revision,
    register_compilation_identity,
)
from .knowledge_models import (
    ASSET_KINDS,
    ASSET_STATUSES,
    MEMORY_TIERS,
    SENSITIVITY_LEVELS,
    SOURCE_KINDS,
    TRUST_LEVELS,
    AssetKind,
    AssetStatus,
    KnowledgeAsset,
    KnowledgeCard,
    KnowledgeSearchResponse,
    MemoryTier,
    Sensitivity,
    SourceKind,
    SourceReference,
    TrustLevel,
    asset_content_sha256,
    canonical_timestamp,
    utc_now,
)
from .store import default_home
from .util import (
    canonical_json,
    compact_text,
    excerpt,
    fts_query,
    has_instruction_risk,
    search_terms,
    search_terms_v1,
    sha256_bytes,
    sha256_file,
    stable_id,
    strict_json_loads,
)

KNOWLEDGE_VAULT_SCHEMA = "deeplaw.knowledge-vault/v1"
KNOWLEDGE_STORAGE_SCHEMA = "deeplaw.knowledge-sqlite/v1"
KNOWLEDGE_EVENT_SCHEMA = "deeplaw.knowledge-event/v1"
KNOWLEDGE_CONTROL_SCHEMA = "deeplaw.knowledge-control/v1"

VaultScope = Literal["personal", "project", "team", "domain"]
VAULT_SCOPES = frozenset(VaultScope.__args__)
RELATION_PREDICATES = frozenset(
    {
        "supports",
        "contradicts",
        "depends_on",
        "implements",
        "derived_from",
        "applies_to",
        "related_to",
    }
)

_VAULT_ID = re.compile(r"^vault_[0-9a-f]{24}$")
_ASSET_ID = re.compile(r"^asset_[0-9a-f]{24}$")
_SOURCE_ID = re.compile(r"^source_[0-9a-f]{24}$")
_SOURCE_KEY = re.compile(r"^sourcekey_[0-9a-f]{24}$")
_FRAGMENT_ID = re.compile(r"^fragment_[0-9a-f]{24}$")
_REVIEW_RECEIPT_ID = re.compile(r"^review_[0-9a-f]{24}$")
_RUN_RECEIPT_ID = re.compile(r"^run_[0-9a-f]{24}$")
_FEEDBACK_ID = re.compile(r"^feedback_[0-9a-f]{24}$")
_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_MAX_MANIFEST_BYTES = 64 * 1024
_MAX_SOURCE_BYTES = 512 * 1024 * 1024
_MAX_FRAGMENT_CHARS = 20_000
_MAX_FRAGMENTS_PER_SOURCE = 100_000
_MAX_BATCH_APPROVAL_ASSETS = _MAX_FRAGMENTS_PER_SOURCE
_MAX_SEARCH_LIMIT = 20
_MAX_SEARCH_CHARS = 20_000
_MAX_EVENT_PAYLOAD_BYTES = 1024 * 1024
_MAX_COMPILER_BYTES = 64 * 1024
_MAX_INTEGRITY_CACHE_ENTRIES = 32
_MAX_PERMISSION_REPORT_SOURCE_DETAILS = 10_000
_MIGRATION_BACKUP_MANIFEST = "migration-backup.json"
_MIGRATION_BACKUP_SCHEMA = "deeplaw.knowledge-migration-backup/v1"
_INTEGRITY_CACHE: OrderedDict[tuple[Any, ...], dict[str, Any]] = OrderedDict()
_INTEGRITY_CACHE_LOCK = RLock()
_MAX_SOURCE_HASH_CACHE_ENTRIES = 256
_SOURCE_HASH_CACHE: OrderedDict[tuple[Any, ...], str] = OrderedDict()
_SOURCE_HASH_CACHE_LOCK = RLock()
_SENSITIVITY_ORDER = ("public", "internal", "private", "restricted")
_KNOWN_EVENT_TYPES = frozenset(
    {
        "vault_initialized",
        "source_compiled",
        "asset_proposed",
        "asset_revision_proposed",
        "projection_edit_proposed",
        "asset_approved",
        "asset_revoked",
        "relation_added",
        "knowledge_control_migrated",
        "source_activated",
        "source_removed",
        "review_recorded",
        "run_receipt_recorded",
        "feedback_recorded",
        "identity_v2_snapshot",
        "search_index_rebuilt",
    }
)


def _maximum_sensitivity(*values: str) -> Sensitivity:
    if not values or any(value not in SENSITIVITY_LEVELS for value in values):
        raise ValueError("cannot combine invalid Knowledge sensitivity values")
    return cast(Sensitivity, max(values, key=_SENSITIVITY_ORDER.index))


def _timestamp_after(candidate: str, prior: str) -> str:
    canonical_timestamp(candidate, field="governance timestamp")
    canonical_timestamp(prior, field="prior governance timestamp")
    if candidate > prior:
        return candidate
    prior_time = datetime.fromisoformat(prior.replace("Z", "+00:00"))
    return (
        (prior_time + timedelta(seconds=1))
        .astimezone(UTC)
        .isoformat(timespec="seconds")
        .replace("+00:00", "Z")
    )


SOURCE_VERSION_STATUSES = frozenset({"pending", "active", "superseded", "removed"})


def default_knowledge_vault() -> Path:
    configured = os.environ.get("DEEPLAW_KNOWLEDGE_VAULT")
    if configured:
        return Path(configured).expanduser().absolute()
    return default_home() / "vaults" / "default"


def _validate_vault_path(path: Path, *, must_exist: bool) -> Path:
    root = path.expanduser().absolute()
    if root.is_symlink():
        raise RuntimeError(f"knowledge vault must not be a symbolic link: {root}")
    if must_exist:
        if not root.is_dir():
            raise FileNotFoundError(f"DeepLaw knowledge vault is missing: {root}")
        if os.name != "nt" and stat.S_IMODE(root.stat().st_mode) & 0o077:
            raise RuntimeError("knowledge vault directory must be accessible only by its owner")
    elif root.exists() and not root.is_dir():
        raise RuntimeError(f"knowledge vault path is not a directory: {root}")
    return root


def _owner_directory(path: Path) -> Path:
    if path.is_symlink():
        raise RuntimeError(f"knowledge vault directory must not be a symbolic link: {path}")
    path.mkdir(parents=True, exist_ok=True, mode=0o700)
    if not path.is_dir():
        raise RuntimeError(f"knowledge vault path is not a directory: {path}")
    os.chmod(path, 0o700)
    return path


def _write_owner_file(path: Path, payload: bytes) -> None:
    if path.is_symlink():
        raise RuntimeError(f"knowledge vault file must not be a symbolic link: {path}")
    temporary = path.with_name(f".{path.name}.{secrets.token_hex(8)}.tmp")
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
    descriptor = os.open(temporary, flags, 0o600)
    try:
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(payload)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
        os.chmod(path, 0o600)
    except BaseException:
        temporary.unlink(missing_ok=True)
        raise


def _manifest_path(root: Path) -> Path:
    return root / "vault.json"


def _database_path(root: Path) -> Path:
    """Return the single trusted Ledger path for a Vault.

    The v0.7 compatibility Vault is created at ``vault.sqlite3``. Installing
    the autonomous v0.8 core atomically promotes that file to
    ``.deeplaw/ledger.sqlite3``. We do not create a symlink or retain two
    copies, so there is never an ambiguous canonical database.
    """

    preferred = root / ".deeplaw" / "ledger.sqlite3"
    legacy = root / "vault.sqlite3"
    if preferred.exists() or preferred.is_symlink():
        return preferred
    if legacy.exists() or legacy.is_symlink():
        return legacy
    return legacy


def promote_legacy_knowledge_ledger(root: str | Path) -> Path:
    """Atomically move a closed legacy Ledger into the v0.8 trusted-core path."""

    vault_root = Path(root).expanduser().absolute()
    legacy = vault_root / "vault.sqlite3"
    preferred = vault_root / ".deeplaw" / "ledger.sqlite3"
    if preferred.exists() or preferred.is_symlink():
        if legacy.exists() or legacy.is_symlink():
            raise RuntimeError("knowledge vault contains two competing Ledger files")
        return preferred
    if legacy.is_symlink() or not legacy.is_file():
        raise RuntimeError("legacy knowledge Ledger is missing or unsafe")
    _owner_directory(preferred.parent)
    connection = sqlite3.connect(legacy)
    try:
        connection.execute("PRAGMA journal_mode = DELETE")
        connection.execute("PRAGMA wal_checkpoint(TRUNCATE)")
        connection.commit()
    finally:
        connection.close()
    for suffix in ("-wal", "-shm"):
        sidecar = Path(f"{legacy}{suffix}")
        if sidecar.exists():
            if sidecar.is_symlink() or not sidecar.is_file():
                raise RuntimeError("legacy knowledge Ledger sidecar is unsafe")
            sidecar.unlink()
    os.replace(legacy, preferred)
    os.chmod(preferred, 0o600)
    return preferred


def _migration_backup_manifest_path(root: Path) -> Path:
    return root / _MIGRATION_BACKUP_MANIFEST


def _stored_source_inventory(root: Path) -> dict[str, Any]:
    sources = root / "sources"
    if sources.is_symlink() or not sources.is_dir():
        raise RuntimeError("knowledge vault sources directory is missing or unsafe")
    inventory: list[dict[str, Any]] = []
    for source in sorted(sources.iterdir(), key=lambda item: item.name):
        if source.is_symlink() or not source.is_file():
            raise RuntimeError("knowledge vault backup contains an unsafe stored source")
        size = source.stat().st_size
        if not 0 <= size <= _MAX_SOURCE_BYTES:
            raise RuntimeError("knowledge vault backup source exceeds its size bound")
        inventory.append(
            {
                "stored_name": source.name,
                "byte_size": size,
                "content_sha256": sha256_file(source),
            }
        )
    return {
        "source_count": len(inventory),
        "inventory_sha256": sha256_bytes(canonical_json(inventory).encode("utf-8")),
    }


def _copy_vault_payload(source_root: Path, destination_root: Path) -> None:
    if destination_root.exists() or destination_root.is_symlink():
        raise FileExistsError(f"knowledge vault copy target already exists: {destination_root}")
    _owner_directory(destination_root)
    _owner_directory(destination_root / "sources")
    _owner_directory(destination_root / ".deeplaw")
    try:
        for source_file in sorted((source_root / "sources").iterdir(), key=lambda item: item.name):
            if source_file.is_symlink() or not source_file.is_file():
                raise RuntimeError("knowledge vault contains an unsafe stored source")
            destination = destination_root / "sources" / source_file.name
            shutil.copyfile(source_file, destination)
            os.chmod(destination, 0o600)
        manifest_destination = _manifest_path(destination_root)
        shutil.copyfile(_manifest_path(source_root), manifest_destination)
        os.chmod(manifest_destination, 0o600)
        source_database = sqlite3.connect(
            f"{_database_path(source_root).as_uri()}?mode=ro",
            uri=True,
        )
        destination_database = sqlite3.connect(_database_path(destination_root))
        try:
            source_database.execute("PRAGMA query_only = ON")
            source_database.backup(destination_database)
            destination_database.commit()
        finally:
            destination_database.close()
            source_database.close()
        os.chmod(_database_path(destination_root), 0o600)
    except BaseException:
        shutil.rmtree(destination_root, ignore_errors=True)
        raise


def create_knowledge_migration_backup(
    path: str | Path,
    *,
    output: str | Path | None = None,
) -> dict[str, Any]:
    """Create and verify a recoverable, owner-only Vault backup before migration."""
    root = _validate_vault_path(Path(path), must_exist=True)
    with KnowledgeVault(root, read_only=True) as vault:
        integrity = vault.verify_integrity()
        if not integrity["valid"]:
            raise RuntimeError("knowledge migration backup requires a healthy source Vault")
        vault_id = vault.vault_id
        revision = vault.revision
        audit_head = vault.audit_head
        control_schema = KNOWLEDGE_CONTROL_SCHEMA if vault.control_enabled else None
    if output is None:
        suffix = utc_now().replace(":", "").replace("-", "")
        destination = root.with_name(
            f"{root.name}.migration-backup-{suffix}-{secrets.token_hex(4)}"
        )
    else:
        destination = Path(output).expanduser().absolute()
    if destination.parent != root.parent:
        destination.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    _copy_vault_payload(root, destination)
    inventory = _stored_source_inventory(destination)
    body = {
        "schema_version": _MIGRATION_BACKUP_SCHEMA,
        "vault_id": vault_id,
        "created_at": utc_now(),
        "revision": revision,
        "audit_head": audit_head,
        "control_schema": control_schema,
        "manifest_sha256": sha256_file(_manifest_path(destination)),
        "database_sha256": sha256_file(_database_path(destination)),
        **inventory,
    }
    backup_sha256 = sha256_bytes(canonical_json(body).encode("utf-8"))
    marker = {**body, "backup_sha256": backup_sha256}
    _write_owner_file(
        _migration_backup_manifest_path(destination),
        (json.dumps(marker, ensure_ascii=False, indent=2, sort_keys=True) + "\n").encode("utf-8"),
    )
    verification = verify_knowledge_migration_backup(destination, expected_vault_id=vault_id)
    if not verification["valid"]:
        raise RuntimeError("knowledge migration backup verification failed")
    return {
        "schema_version": "deeplaw.knowledge-migration-backup-result/v1",
        "backup_path": str(destination),
        **marker,
        "valid": True,
    }


def verify_knowledge_migration_backup(
    path: str | Path,
    *,
    expected_vault_id: str | None = None,
) -> dict[str, Any]:
    root = _validate_vault_path(Path(path), must_exist=True)
    marker_path = _migration_backup_manifest_path(root)
    try:
        if (
            marker_path.is_symlink()
            or not marker_path.is_file()
            or not 1 <= marker_path.stat().st_size <= _MAX_MANIFEST_BYTES
            or (os.name != "nt" and stat.S_IMODE(marker_path.stat().st_mode) & 0o077)
        ):
            raise ValueError("knowledge migration backup marker is unsafe")
        marker = strict_json_loads(marker_path.read_bytes())
        expected_fields = {
            "schema_version",
            "vault_id",
            "created_at",
            "revision",
            "audit_head",
            "control_schema",
            "manifest_sha256",
            "database_sha256",
            "source_count",
            "inventory_sha256",
            "backup_sha256",
        }
        if not isinstance(marker, dict) or set(marker) != expected_fields:
            raise ValueError("knowledge migration backup marker contract is invalid")
        body = {key: marker[key] for key in expected_fields if key != "backup_sha256"}
        marker_valid = (
            marker["schema_version"] == _MIGRATION_BACKUP_SCHEMA
            and isinstance(marker["vault_id"], str)
            and bool(_VAULT_ID.fullmatch(marker["vault_id"]))
            and (expected_vault_id is None or marker["vault_id"] == expected_vault_id)
            and canonical_timestamp(marker["created_at"], field="backup created_at")
            == marker["created_at"]
            and isinstance(marker["revision"], int)
            and not isinstance(marker["revision"], bool)
            and marker["revision"] >= 0
            and isinstance(marker["audit_head"], str)
            and bool(_SHA256.fullmatch(marker["audit_head"]))
            and marker["control_schema"] in {None, KNOWLEDGE_CONTROL_SCHEMA}
            and isinstance(marker["source_count"], int)
            and not isinstance(marker["source_count"], bool)
            and marker["source_count"] >= 0
            and all(
                isinstance(marker[field], str) and bool(_SHA256.fullmatch(marker[field]))
                for field in (
                    "manifest_sha256",
                    "database_sha256",
                    "inventory_sha256",
                    "backup_sha256",
                )
            )
            and marker["backup_sha256"] == sha256_bytes(canonical_json(body).encode("utf-8"))
            and marker["manifest_sha256"] == sha256_file(_manifest_path(root))
            and marker["database_sha256"] == sha256_file(_database_path(root))
        )
        inventory = _stored_source_inventory(root)
        inventory_valid = (
            inventory["source_count"] == marker["source_count"]
            and inventory["inventory_sha256"] == marker["inventory_sha256"]
        )
        with KnowledgeVault(root, read_only=True) as vault:
            vault_valid = (
                vault.vault_id == marker["vault_id"]
                and vault.revision == marker["revision"]
                and vault.audit_head == marker["audit_head"]
                and vault.verify_integrity()["valid"]
            )
    except (KeyError, OSError, RuntimeError, sqlite3.DatabaseError, TypeError, ValueError):
        marker = {}
        marker_valid = False
        inventory_valid = False
        vault_valid = False
    return {
        "schema_version": "deeplaw.knowledge-migration-backup-verification/v1",
        "backup_path": str(root),
        "vault_id": marker.get("vault_id"),
        "marker_valid": marker_valid,
        "inventory_valid": inventory_valid,
        "vault_valid": vault_valid,
        "valid": bool(marker_valid and inventory_valid and vault_valid),
    }


def restore_knowledge_migration_backup(
    path: str | Path,
    *,
    backup: str | Path,
    confirm: bool,
) -> dict[str, Any]:
    """Atomically restore a verified backup while retaining the replaced Vault."""
    if not confirm:
        raise ValueError("knowledge migration rollback requires explicit confirmation")
    root = _validate_vault_path(Path(path), must_exist=True)
    with KnowledgeVault(root, read_only=True) as vault:
        vault_id = vault.vault_id
    backup_root = _validate_vault_path(Path(backup), must_exist=True)
    verification = verify_knowledge_migration_backup(
        backup_root,
        expected_vault_id=vault_id,
    )
    if not verification["valid"]:
        raise RuntimeError("knowledge migration rollback requires a valid matching backup")
    token = secrets.token_hex(6)
    replacement = root.with_name(f".{root.name}.restore-{token}.tmp")
    retained = root.with_name(f"{root.name}.pre-rollback-{token}")
    _copy_vault_payload(backup_root, replacement)
    try:
        with KnowledgeVault(replacement, read_only=True) as restored:
            if restored.vault_id != vault_id or not restored.verify_integrity()["valid"]:
                raise RuntimeError("restored knowledge Vault failed pre-swap verification")
        os.replace(root, retained)
        try:
            os.replace(replacement, root)
        except BaseException:
            os.replace(retained, root)
            raise
        with KnowledgeVault(root, read_only=True) as restored:
            restored_valid = restored.verify_integrity()["valid"]
            revision = restored.revision
            audit_head = restored.audit_head
        if not restored_valid:
            failed = root.with_name(f"{root.name}.failed-rollback-{token}")
            os.replace(root, failed)
            os.replace(retained, root)
            raise RuntimeError("restored knowledge Vault failed post-swap verification")
    except BaseException:
        if replacement.exists():
            shutil.rmtree(replacement)
        raise
    return {
        "schema_version": "deeplaw.knowledge-migration-rollback/v1",
        "vault_id": vault_id,
        "backup_path": str(backup_root),
        "retained_previous_vault": str(retained),
        "revision": revision,
        "audit_head": audit_head,
        "restored": True,
        "valid": True,
    }


def knowledge_vault_permission_report(path: str | Path) -> dict[str, Any]:
    """Inspect filesystem isolation without treating POSIX modes as Windows ACLs."""
    root = Path(path).expanduser().absolute()
    protected = (
        ("vault_root", root, "directory"),
        ("manifest", _manifest_path(root), "file"),
        ("database", _database_path(root), "file"),
        ("sources", root / "sources", "directory"),
    )
    entries: list[dict[str, Any]] = []
    for label, protected_path, expected_kind in protected:
        symlink = protected_path.is_symlink()
        exists = protected_path.exists() or symlink
        actual_kind = (
            "symlink"
            if symlink
            else "directory"
            if protected_path.is_dir()
            else "file"
            if protected_path.is_file()
            else "missing"
        )
        mode: str | None = None
        owner_only: bool | None = None
        if exists and not symlink and os.name != "nt":
            mode_bits = stat.S_IMODE(protected_path.stat().st_mode)
            mode = f"{mode_bits:04o}"
            owner_only = not bool(mode_bits & 0o077)
        entries.append(
            {
                "label": label,
                "expected_kind": expected_kind,
                "actual_kind": actual_kind,
                "symlink": symlink,
                "posix_mode": mode,
                "owner_only": owner_only,
            }
        )

    stored_sources: list[dict[str, Any]] = []
    stored_source_files_checked = 0
    stored_sources_scan_complete = True
    stored_sources_structural_valid = True
    stored_sources_owner_only = True
    sources = root / "sources"
    if sources.is_dir() and not sources.is_symlink():
        try:
            for stored_path in sources.iterdir():
                stored_source_files_checked += 1
                symlink = stored_path.is_symlink()
                regular_file = stored_path.is_file() and not symlink
                mode = None
                owner_only = None
                if regular_file and os.name != "nt":
                    mode_bits = stat.S_IMODE(stored_path.stat().st_mode)
                    mode = f"{mode_bits:04o}"
                    owner_only = not bool(mode_bits & 0o077)
                    stored_sources_owner_only = stored_sources_owner_only and owner_only
                kind = "symlink" if symlink else "file" if regular_file else "other"
                stored_sources_structural_valid = stored_sources_structural_valid and kind == "file"
                if len(stored_sources) < _MAX_PERMISSION_REPORT_SOURCE_DETAILS:
                    stored_sources.append(
                        {
                            "stored_name": stored_path.name,
                            "kind": kind,
                            "posix_mode": mode,
                            "owner_only": owner_only,
                        }
                    )
        except OSError:
            stored_sources_scan_complete = False

    stored_sources.sort(key=lambda item: item["stored_name"])
    stored_sources_truncated = stored_source_files_checked > len(stored_sources)

    structural_valid = (
        all(
            entry["actual_kind"] == entry["expected_kind"] and not entry["symlink"]
            for entry in entries
        )
        and stored_sources_structural_valid
    )
    if os.name == "nt":
        from .windows_acl import native_windows_acl_report

        try:
            native_acl = native_windows_acl_report(root)
            permissions_verified = bool(structural_valid and native_acl["permissions_verified"])
            status = "verified" if permissions_verified else "failed"
            notes = [] if permissions_verified else list(native_acl.get("errors", []))
        except (OSError, RuntimeError, ValueError) as error:
            native_acl = {
                "schema_version": "deeplaw.windows-acl-report/v1",
                "permissions_verified": False,
                "errors": [str(error)],
                "entries": [],
            }
            permissions_verified = False
            status = "not_verified"
            notes = [str(error)]
        security_model = "windows_native_acl_owner_only"
    else:
        owner_only = (
            all(entry["owner_only"] is True for entry in entries) and stored_sources_owner_only
        )
        permissions_verified = structural_valid and owner_only and stored_sources_scan_complete
        status = (
            "verified"
            if permissions_verified
            else "not_verified"
            if not stored_sources_scan_complete
            else "failed"
        )
        security_model = "posix_owner_only_mode"
        notes = (
            []
            if stored_sources_scan_complete
            else ["Stored source permission scan did not complete; isolation is not verified."]
        )
    return {
        "schema_version": "deeplaw.knowledge-permission-report/v1",
        "platform": os.name,
        "status": status,
        "permissions_verified": permissions_verified,
        "structural_valid": structural_valid,
        "security_model": security_model,
        "entries": entries,
        "stored_source_files_checked": stored_source_files_checked,
        "stored_source_files_returned": len(stored_sources),
        "stored_source_files_truncated": stored_sources_truncated,
        "stored_sources": stored_sources,
        "native_windows_acl": native_acl if os.name == "nt" else None,
        "notes": notes,
    }


def _validate_manifest(value: Any) -> dict[str, Any]:
    expected = {
        "schema_version",
        "storage_schema",
        "vault_id",
        "name",
        "scope",
        "created_at",
    }
    if not isinstance(value, dict) or set(value) != expected:
        raise RuntimeError("knowledge vault manifest does not match its closed contract")
    if value.get("schema_version") != KNOWLEDGE_VAULT_SCHEMA:
        raise RuntimeError("unsupported knowledge vault manifest schema")
    if value.get("storage_schema") != KNOWLEDGE_STORAGE_SCHEMA:
        raise RuntimeError("unsupported knowledge vault storage schema")
    vault_id = value.get("vault_id")
    if not isinstance(vault_id, str) or not _VAULT_ID.fullmatch(vault_id):
        raise RuntimeError("knowledge vault ID is invalid")
    name = value.get("name")
    if not isinstance(name, str) or not name or name != name.strip() or len(name) > 200:
        raise RuntimeError("knowledge vault name is invalid")
    if value.get("scope") not in VAULT_SCOPES:
        raise RuntimeError("knowledge vault scope is invalid")
    try:
        canonical_timestamp(value.get("created_at"), field="vault created_at")
    except (TypeError, ValueError) as error:
        raise RuntimeError("knowledge vault created_at is invalid") from error
    return value


def _load_manifest(root: Path) -> dict[str, Any]:
    path = _manifest_path(root)
    if path.is_symlink() or not path.is_file() or path.stat().st_size > _MAX_MANIFEST_BYTES:
        raise RuntimeError("knowledge vault manifest is missing, unsafe, or too large")
    try:
        value = strict_json_loads(path.read_bytes())
    except (OSError, UnicodeDecodeError, json.JSONDecodeError, ValueError) as error:
        raise RuntimeError("knowledge vault manifest is invalid") from error
    return _validate_manifest(value)


def _token_string(text: str) -> str:
    return " ".join(search_terms(text))


def _token_string_v1(text: str) -> str:
    return " ".join(search_terms_v1(text))


def _source_membership_sha256(
    fragment_ids: Iterable[str],
    asset_ids: Iterable[str],
) -> str:
    pairs = [
        {"fragment_id": fragment_id, "asset_id": asset_id}
        for fragment_id, asset_id in zip(fragment_ids, asset_ids, strict=True)
    ]
    return sha256_bytes(canonical_json(pairs).encode("utf-8"))


def knowledge_source_key(
    *,
    vault_id: str,
    source_kind: SourceKind,
    source_path: str | Path,
    origin_uri: str | None,
    collection_id: str | None = None,
    logical_path: str | None = None,
) -> str:
    """Return a move-stable logical identity without hashing an absolute path."""
    if source_kind not in SOURCE_KINDS:
        raise ValueError("unsupported knowledge source kind")
    selected_collection = collection_id or make_collection_id(
        vault_id=vault_id,
        name="project",
    )
    if logical_path is not None:
        selected_path = normalize_logical_path(logical_path)
    elif origin_uri is not None:
        commitment = canonical_origin_commitment(origin_uri)
        selected_path = f"origins/{sha256_bytes(commitment.encode('utf-8'))}"
    else:
        selected_path = normalize_logical_path(Path(source_path).name)
    return make_source_key(
        collection_id=selected_collection,
        logical_path=selected_path,
    )


def _control_tables_sql() -> str:
    return """
        CREATE TABLE IF NOT EXISTS source_lifecycle (
            source_id TEXT PRIMARY KEY REFERENCES sources(source_id),
            source_key TEXT NOT NULL,
            previous_source_id TEXT REFERENCES sources(source_id),
            status TEXT NOT NULL,
            activated_at TEXT,
            superseded_at TEXT,
            removed_at TEXT
        ) WITHOUT ROWID;
        CREATE INDEX IF NOT EXISTS source_lifecycle_key_status
            ON source_lifecycle(source_key, status);
        CREATE UNIQUE INDEX IF NOT EXISTS active_source_key
            ON source_lifecycle(source_key)
            WHERE status = 'active';

        CREATE TABLE IF NOT EXISTS review_receipts (
            review_receipt_id TEXT PRIMARY KEY,
            reviewer_id TEXT NOT NULL,
            reviewed_at TEXT NOT NULL,
            policy_id TEXT NOT NULL,
            source_id TEXT REFERENCES sources(source_id),
            proposal_ids_json TEXT NOT NULL,
            asset_hashes_json TEXT NOT NULL,
            decisions_json TEXT NOT NULL,
            reason TEXT NOT NULL,
            review_manifest_sha256 TEXT NOT NULL,
            signature_json TEXT,
            receipt_sha256 TEXT NOT NULL
        ) WITHOUT ROWID;

        CREATE TABLE IF NOT EXISTS run_receipts (
            run_id TEXT PRIMARY KEY,
            payload_json TEXT NOT NULL,
            receipt_sha256 TEXT NOT NULL,
            created_at TEXT NOT NULL
        ) WITHOUT ROWID;

        CREATE TABLE IF NOT EXISTS feedback_records (
            feedback_id TEXT PRIMARY KEY,
            run_id TEXT NOT NULL REFERENCES run_receipts(run_id),
            payload_json TEXT NOT NULL,
            receipt_sha256 TEXT NOT NULL,
            created_at TEXT NOT NULL,
            proposal_asset_id TEXT REFERENCES assets(asset_id)
        ) WITHOUT ROWID;
    """


def _install_control_tables(connection: sqlite3.Connection) -> None:
    for statement in _control_tables_sql().split(";"):
        if statement.strip():
            connection.execute(statement)


def initialize_knowledge_vault(
    path: str | Path,
    *,
    name: str,
    scope: VaultScope,
) -> dict[str, Any]:
    root = _validate_vault_path(Path(path), must_exist=False)
    if scope not in VAULT_SCOPES:
        raise ValueError(f"unsupported knowledge vault scope: {scope}")
    name = name.strip()
    if not name or len(name) > 200:
        raise ValueError("knowledge vault name must be between 1 and 200 characters")
    if root.exists() and any(root.iterdir()):
        manifest = _load_manifest(root)
        if manifest["name"] != name or manifest["scope"] != scope:
            raise RuntimeError("existing knowledge vault identity does not match the request")
        with KnowledgeVault(root, read_only=True) as vault:
            return vault.inspect()

    _owner_directory(root)
    _owner_directory(root / "sources")
    _owner_directory(root / ".deeplaw")
    created_at = utc_now()
    vault_id = stable_id("vault", secrets.token_hex(32))
    manifest = {
        "schema_version": KNOWLEDGE_VAULT_SCHEMA,
        "storage_schema": KNOWLEDGE_STORAGE_SCHEMA,
        "vault_id": vault_id,
        "name": name,
        "scope": scope,
        "created_at": created_at,
    }
    _write_owner_file(
        _manifest_path(root),
        (json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True) + "\n").encode("utf-8"),
    )
    database = _database_path(root)
    if database.exists() or database.is_symlink():
        raise RuntimeError("knowledge vault database already exists")
    connection = sqlite3.connect(database)
    try:
        connection.executescript(
            """
            PRAGMA journal_mode = DELETE;
            PRAGMA synchronous = FULL;
            PRAGMA foreign_keys = ON;

            CREATE TABLE metadata (
                key TEXT PRIMARY KEY,
                value TEXT NOT NULL
            ) WITHOUT ROWID;

            CREATE TABLE sources (
                source_id TEXT PRIMARY KEY,
                kind TEXT NOT NULL,
                title TEXT NOT NULL,
                origin_uri TEXT,
                stored_name TEXT,
                media_type TEXT NOT NULL,
                byte_size INTEGER NOT NULL,
                content_sha256 TEXT NOT NULL,
                trust TEXT NOT NULL,
                sensitivity TEXT NOT NULL,
                imported_at TEXT NOT NULL,
                instruction_risk INTEGER NOT NULL,
                warnings_json TEXT NOT NULL,
                compiler_json TEXT NOT NULL
            ) WITHOUT ROWID;

            CREATE TABLE source_fragments (
                fragment_id TEXT PRIMARY KEY,
                source_id TEXT NOT NULL REFERENCES sources(source_id),
                ordinal INTEGER NOT NULL,
                locator TEXT NOT NULL,
                text TEXT NOT NULL,
                text_sha256 TEXT NOT NULL,
                instruction_risk INTEGER NOT NULL,
                UNIQUE(source_id, ordinal)
            ) WITHOUT ROWID;

            CREATE TABLE assets (
                asset_id TEXT PRIMARY KEY,
                kind TEXT NOT NULL,
                memory_tier TEXT NOT NULL,
                title TEXT NOT NULL,
                statement TEXT NOT NULL,
                semantic_key TEXT,
                status TEXT NOT NULL,
                verification TEXT NOT NULL,
                trust TEXT NOT NULL,
                sensitivity TEXT NOT NULL,
                source_refs_json TEXT NOT NULL,
                tags_json TEXT NOT NULL,
                warnings_json TEXT NOT NULL,
                created_at TEXT NOT NULL,
                activated_at TEXT,
                expires_at TEXT,
                supersedes_asset_id TEXT REFERENCES assets(asset_id),
                origin_uri TEXT,
                content_sha256 TEXT NOT NULL
            ) WITHOUT ROWID;

            CREATE UNIQUE INDEX active_asset_semantic_key
                ON assets(semantic_key)
                WHERE status = 'active' AND semantic_key IS NOT NULL;
            CREATE INDEX assets_status_tier_kind
                ON assets(status, memory_tier, kind);
            CREATE INDEX assets_expiry
                ON assets(status, expires_at);

            CREATE TABLE relations (
                relation_id TEXT PRIMARY KEY,
                subject_asset_id TEXT NOT NULL REFERENCES assets(asset_id),
                predicate TEXT NOT NULL,
                object_asset_id TEXT NOT NULL REFERENCES assets(asset_id),
                evidence_fragment_id TEXT REFERENCES source_fragments(fragment_id),
                verification TEXT NOT NULL,
                created_at TEXT NOT NULL,
                CHECK(subject_asset_id <> object_asset_id),
                UNIQUE(subject_asset_id, predicate, object_asset_id)
            ) WITHOUT ROWID;
            CREATE INDEX relations_object_asset_id
                ON relations(object_asset_id);

            CREATE TABLE events (
                sequence INTEGER PRIMARY KEY,
                schema_version TEXT NOT NULL,
                event_type TEXT NOT NULL,
                object_id TEXT,
                payload_json TEXT NOT NULL,
                previous_hash TEXT,
                event_hash TEXT NOT NULL,
                created_at TEXT NOT NULL
            );

            CREATE VIRTUAL TABLE asset_search USING fts5(
                asset_id UNINDEXED,
                title_tokens,
                statement_tokens,
                semantic_tokens,
                tag_tokens,
                tokenize = 'unicode61 remove_diacritics 2'
            );
            """
        )
        _install_control_tables(connection)
        install_identity_tables(
            connection,
            installed_at=created_at,
            migration_source="new-vault",
        )
        metadata = {
            "schema_version": KNOWLEDGE_STORAGE_SCHEMA,
            "control_schema": KNOWLEDGE_CONTROL_SCHEMA,
            "vault_id": vault_id,
            "name": name,
            "scope": scope,
            "created_at": created_at,
            "revision": "0",
            "audit_head": "",
        }
        connection.executemany(
            "INSERT INTO metadata(key, value) VALUES (?, ?)",
            metadata.items(),
        )
        event_payload = {
            "schema_version": KNOWLEDGE_EVENT_SCHEMA,
            "sequence": 0,
            "event_type": "vault_initialized",
            "object_id": vault_id,
            "payload": {"name": name, "scope": scope},
            "previous_hash": None,
            "created_at": created_at,
        }
        event_hash = sha256_bytes(canonical_json(event_payload).encode("utf-8"))
        connection.execute(
            "INSERT INTO events VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (
                0,
                KNOWLEDGE_EVENT_SCHEMA,
                "vault_initialized",
                vault_id,
                canonical_json({"name": name, "scope": scope}),
                None,
                event_hash,
                created_at,
            ),
        )
        connection.execute(
            "UPDATE metadata SET value = ? WHERE key = 'audit_head'",
            (event_hash,),
        )
        connection.commit()
    finally:
        connection.close()
    os.chmod(database, 0o600)
    if os.name == "nt":
        from .windows_acl import harden_windows_vault

        harden_windows_vault(root)
    return {
        **manifest,
        "revision": 0,
        "audit_head": event_hash,
        "path": str(root),
    }


class KnowledgeVault(AbstractContextManager["KnowledgeVault"]):
    def __init__(self, path: str | Path | None = None, *, read_only: bool = True) -> None:
        self.root = _validate_vault_path(
            Path(path) if path is not None else default_knowledge_vault(),
            must_exist=True,
        )
        self.manifest = _load_manifest(self.root)
        database = _database_path(self.root)
        if database.is_symlink() or not database.is_file():
            raise RuntimeError("knowledge vault database is missing or unsafe")
        sources_directory = self.root / "sources"
        if sources_directory.is_symlink() or not sources_directory.is_dir():
            raise RuntimeError("knowledge vault sources directory is missing or unsafe")
        if os.name != "nt":
            for protected_path in (
                _manifest_path(self.root),
                database,
                sources_directory,
            ):
                if stat.S_IMODE(protected_path.stat().st_mode) & 0o077:
                    raise RuntimeError(
                        "knowledge vault files must be accessible only by their owner"
                    )
        self.database = database
        self.read_only = read_only
        self._opened_database_fingerprint = self._database_file_fingerprint()
        self._integrity_cache_key: tuple[Any, ...] | None = None
        self._integrity_cache_value: dict[str, Any] | None = None
        if read_only:
            uri = f"{database.as_uri()}?mode=ro"
            self.connection = sqlite3.connect(uri, uri=True)
            self.connection.execute("PRAGMA query_only = ON")
        else:
            self.connection = sqlite3.connect(database)
            self.connection.execute("PRAGMA journal_mode = DELETE")
            self.connection.execute("PRAGMA synchronous = FULL")
        try:
            self.connection.row_factory = sqlite3.Row
            self.connection.execute("PRAGMA foreign_keys = ON")
            self.connection.execute("PRAGMA busy_timeout = 5000")
            if self.read_only:
                self.connection.execute("BEGIN")
            self._validate_identity()
            if (
                self.read_only
                and self._database_file_fingerprint() != self._opened_database_fingerprint
            ):
                raise RuntimeError(
                    "knowledge vault database changed while its read snapshot was opening"
                )
        except BaseException:
            self.connection.close()
            raise

    def __exit__(self, *args: object) -> None:
        self.close()

    def close(self) -> None:
        self.connection.close()

    @property
    def vault_id(self) -> str:
        return cast(str, self.manifest["vault_id"])

    @property
    def revision(self) -> int:
        row = self.connection.execute(
            "SELECT value FROM metadata WHERE key = 'revision'"
        ).fetchone()
        if row is None:
            raise RuntimeError("knowledge vault revision is missing")
        try:
            revision = int(row["value"])
        except (TypeError, ValueError) as error:
            raise RuntimeError("knowledge vault revision is invalid") from error
        if revision < 0:
            raise RuntimeError("knowledge vault revision is invalid")
        return revision

    @property
    def audit_head(self) -> str:
        row = self.connection.execute(
            "SELECT value FROM metadata WHERE key = 'audit_head'"
        ).fetchone()
        if row is None or not _SHA256.fullmatch(row["value"]):
            raise RuntimeError("knowledge vault audit head is invalid")
        return cast(str, row["value"])

    def _validate_identity(self) -> None:
        rows = self.connection.execute("SELECT key, value FROM metadata").fetchall()
        metadata = {row["key"]: row["value"] for row in rows}
        base_expected = {
            "schema_version",
            "vault_id",
            "name",
            "scope",
            "created_at",
            "revision",
            "audit_head",
        }
        control_expected = {*base_expected, "control_schema"}
        metadata_fields = frozenset(metadata)
        if metadata_fields not in {frozenset(base_expected), frozenset(control_expected)}:
            raise RuntimeError("knowledge vault metadata does not match its closed contract")
        if metadata["schema_version"] != KNOWLEDGE_STORAGE_SCHEMA:
            raise RuntimeError("unsupported knowledge vault database schema")
        self.control_enabled = "control_schema" in metadata
        if self.control_enabled:
            if metadata["control_schema"] != KNOWLEDGE_CONTROL_SCHEMA:
                raise RuntimeError("unsupported knowledge control schema")
            required_tables = {
                "source_lifecycle",
                "review_receipts",
                "run_receipts",
                "feedback_records",
            }
            available_tables = {
                row["name"]
                for row in self.connection.execute(
                    "SELECT name FROM sqlite_master WHERE type = 'table'"
                )
            }
            if not required_tables.issubset(available_tables):
                raise RuntimeError("knowledge control tables are missing")
        identity_table_names = {
            row["name"]
            for row in self.connection.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table'"
            )
        }
        identity_marker_present = "identity_v2" in identity_table_names
        self.identity_v2_enabled = identity_tables_present(self.connection)
        if identity_marker_present and not self.identity_v2_enabled:
            raise RuntimeError("Knowledge Identity v2 tables are incomplete")
        for field in ("vault_id", "name", "scope", "created_at"):
            if metadata[field] != str(self.manifest[field]):
                raise RuntimeError(f"knowledge vault manifest/database {field} mismatch")
        _ = self.revision
        _ = self.audit_head

    def _require_write(self) -> None:
        if self.read_only:
            raise RuntimeError("knowledge vault is open read-only")

    def _require_control(self) -> None:
        if not self.control_enabled:
            raise RuntimeError(
                "knowledge control schema is not installed; run knowledge migrate --apply"
            )

    def verify_knowledge_control_migration(self) -> dict[str, Any]:
        source_count = self.connection.execute("SELECT COUNT(*) FROM sources").fetchone()[0]
        if not self.control_enabled:
            return {
                "schema_version": "deeplaw.knowledge-migration-verification/v1",
                "vault_id": self.vault_id,
                "control_schema": None,
                "source_count": source_count,
                "source_lifecycle_count": 0,
                "integrity_valid": self.verify_integrity()["valid"],
                "valid": False,
            }
        lifecycle_count = self.connection.execute(
            "SELECT COUNT(*) FROM source_lifecycle"
        ).fetchone()[0]
        missing_lifecycle = self.connection.execute(
            """
            SELECT COUNT(*)
            FROM sources
            LEFT JOIN source_lifecycle USING(source_id)
            WHERE source_lifecycle.source_id IS NULL
            """
        ).fetchone()[0]
        invalid_status = self.connection.execute(
            """
            SELECT COUNT(*) FROM source_lifecycle
            WHERE status NOT IN ('pending', 'active', 'superseded', 'removed')
            """
        ).fetchone()[0]
        integrity_valid = self.verify_integrity()["valid"]
        valid = (
            lifecycle_count == source_count
            and missing_lifecycle == 0
            and invalid_status == 0
            and integrity_valid
        )
        return {
            "schema_version": "deeplaw.knowledge-migration-verification/v1",
            "vault_id": self.vault_id,
            "control_schema": KNOWLEDGE_CONTROL_SCHEMA,
            "source_count": source_count,
            "source_lifecycle_count": lifecycle_count,
            "missing_lifecycle_count": missing_lifecycle,
            "invalid_status_count": invalid_status,
            "integrity_valid": integrity_valid,
            "valid": valid,
        }

    def migrate_knowledge_control(
        self,
        *,
        apply: bool,
        backup_path: str | Path | None = None,
    ) -> dict[str, Any]:
        """Install the additive v1 control-plane schema for a legacy vault."""
        source_count = self.connection.execute("SELECT COUNT(*) FROM sources").fetchone()[0]
        plan = {
            "schema_version": "deeplaw.knowledge-migration-plan/v1",
            "vault_id": self.vault_id,
            "from_control_schema": (KNOWLEDGE_CONTROL_SCHEMA if self.control_enabled else None),
            "to_control_schema": KNOWLEDGE_CONTROL_SCHEMA,
            "source_count": source_count,
            "required": not self.control_enabled,
            "applied": False,
            "backup_required": not self.control_enabled,
        }
        if not apply or self.control_enabled:
            return plan
        self._require_write()
        backup = create_knowledge_migration_backup(self.root, output=backup_path)
        try:
            self.connection.execute("BEGIN IMMEDIATE")
            self._require_healthy_integrity()
            _install_control_tables(self.connection)
            mappings: list[dict[str, Any]] = []
            for source in self.connection.execute(
                "SELECT source_id FROM sources ORDER BY source_id"
            ):
                source_id = source["source_id"]
                active = self.connection.execute(
                    """
                    SELECT MAX(activated_at) AS activated_at
                    FROM assets, json_each(assets.source_refs_json) AS reference
                    WHERE json_extract(reference.value, '$.source_id') = ?
                      AND assets.status = 'active'
                    """,
                    (source_id,),
                ).fetchone()
                status = "active" if active["activated_at"] is not None else "pending"
                source_key = stable_id(
                    "sourcekey",
                    self.vault_id,
                    "legacy-source",
                    source_id,
                )
                self.connection.execute(
                    """
                    INSERT INTO source_lifecycle(
                        source_id, source_key, previous_source_id, status,
                        activated_at, superseded_at, removed_at
                    ) VALUES (?, ?, NULL, ?, ?, NULL, NULL)
                    """,
                    (source_id, source_key, status, active["activated_at"]),
                )
                mappings.append(
                    {
                        "source_id": source_id,
                        "source_key": source_key,
                        "status": status,
                    }
                )
            mapping_sha256 = sha256_bytes(canonical_json(mappings).encode("utf-8"))
            self.connection.execute(
                "INSERT INTO metadata(key, value) VALUES ('control_schema', ?)",
                (KNOWLEDGE_CONTROL_SCHEMA,),
            )
            revision, audit_head = self._append_event(
                event_type="knowledge_control_migrated",
                object_id=self.vault_id,
                payload={
                    "control_schema": KNOWLEDGE_CONTROL_SCHEMA,
                    "source_count": len(mappings),
                    "mapping_sha256": mapping_sha256,
                },
            )
            self.connection.commit()
            self.control_enabled = True
        except BaseException:
            self.connection.rollback()
            raise
        verification = self.verify_knowledge_control_migration()
        if not verification["valid"]:
            raise RuntimeError(
                "knowledge control migration committed but failed verification; "
                f"restore the verified backup at {backup['backup_path']}"
            )
        return {
            **plan,
            "required": True,
            "applied": True,
            "backup": backup,
            "verification": verification,
            "revision": revision,
            "audit_head": audit_head,
            "mapping_sha256": mapping_sha256,
        }

    def verify_identity_v2_migration(self) -> dict[str, Any]:
        source_count = self.connection.execute("SELECT COUNT(*) FROM sources").fetchone()[0]
        fragment_count = self.connection.execute(
            "SELECT COUNT(*) FROM source_fragments"
        ).fetchone()[0]
        source_bound_asset_count = self.connection.execute(
            "SELECT COUNT(*) FROM assets WHERE json_array_length(source_refs_json) > 0"
        ).fetchone()[0]
        if not self.identity_v2_enabled:
            return {
                "schema_version": "deeplaw.identity-v2-migration-verification/v1",
                "vault_id": self.vault_id,
                "identity_schema": None,
                "source_count": source_count,
                "source_binding_count": 0,
                "fragment_count": fragment_count,
                "fragment_binding_count": 0,
                "source_bound_asset_count": source_bound_asset_count,
                "asset_binding_count": 0,
                "integrity_valid": self.verify_integrity()["valid"],
                "valid": False,
            }
        counts = {
            "source_binding_count": self.connection.execute(
                "SELECT COUNT(*) FROM source_revision_bindings_v2"
            ).fetchone()[0],
            "build_binding_count": self.connection.execute(
                "SELECT COUNT(*) FROM source_build_bindings_v2"
            ).fetchone()[0],
            "location_count": self.connection.execute(
                "SELECT COUNT(*) FROM source_locations_v2"
            ).fetchone()[0],
            "fragment_binding_count": self.connection.execute(
                "SELECT COUNT(*) FROM legacy_fragment_bindings_v2"
            ).fetchone()[0],
            "asset_binding_count": self.connection.execute(
                "SELECT COUNT(*) FROM asset_revision_bindings_v2"
            ).fetchone()[0],
        }
        integrity_valid = self.verify_integrity()["valid"]
        valid = bool(
            counts["source_binding_count"] == source_count
            and counts["build_binding_count"] == source_count
            and counts["location_count"] == source_count
            and counts["fragment_binding_count"] == fragment_count
            and counts["asset_binding_count"] == source_bound_asset_count
            and integrity_valid
        )
        return {
            "schema_version": "deeplaw.identity-v2-migration-verification/v1",
            "vault_id": self.vault_id,
            "identity_schema": "deeplaw.knowledge-identity/v2",
            "source_count": source_count,
            "fragment_count": fragment_count,
            "source_bound_asset_count": source_bound_asset_count,
            **counts,
            "integrity_valid": integrity_valid,
            "valid": valid,
        }

    def migrate_identity_v2(
        self,
        *,
        apply: bool,
        backup_path: str | Path | None = None,
    ) -> dict[str, Any]:
        """Install and backfill the additive Identity v2 projection."""
        self._require_control()
        source_count = self.connection.execute("SELECT COUNT(*) FROM sources").fetchone()[0]
        plan = {
            "schema_version": "deeplaw.identity-v2-migration-plan/v1",
            "vault_id": self.vault_id,
            "from_identity_schema": (
                "deeplaw.knowledge-identity/v2" if self.identity_v2_enabled else None
            ),
            "to_identity_schema": "deeplaw.knowledge-identity/v2",
            "source_count": source_count,
            "required": not self.identity_v2_enabled,
            "applied": False,
            "backup_required": not self.identity_v2_enabled,
        }
        if not apply or self.identity_v2_enabled:
            return plan
        self._require_write()
        backup = create_knowledge_migration_backup(self.root, output=backup_path)
        try:
            self.connection.execute("BEGIN IMMEDIATE")
            self._require_healthy_integrity()
            installed_at = utc_now()
            install_identity_tables(
                self.connection,
                installed_at=installed_at,
                migration_source="knowledge-sqlite/v1",
            )
            self.identity_v2_enabled = True
            source_rows = self.connection.execute(
                """
                SELECT sources.*, source_lifecycle.source_key AS legacy_source_key,
                       source_lifecycle.status AS lifecycle_status
                FROM sources
                JOIN source_lifecycle USING(source_id)
                ORDER BY sources.imported_at, sources.source_id
                """
            ).fetchall()
            legacy_keys = sorted({row["legacy_source_key"] for row in source_rows})
            path_by_legacy_key = {
                key: f"migrated/source-{index:06d}"
                for index, key in enumerate(legacy_keys, start=1)
            }
            pending = list(source_rows)
            migrated_source_ids: set[str] = set()
            migrated_asset_ids: set[str] = set()
            identity_records: list[dict[str, Any]] = []
            while pending:
                made_progress = False
                deferred: list[sqlite3.Row] = []
                for source in pending:
                    owned_assets: list[tuple[KnowledgeAsset, tuple[SourceReference, ...]]] = []
                    blocked = False
                    for asset_row in self.connection.execute(
                        "SELECT * FROM assets ORDER BY created_at, asset_id"
                    ):
                        asset = self._row_to_asset(asset_row)
                        if (
                            not asset.source_refs
                            or asset.source_refs[0].source_id != source["source_id"]
                        ):
                            continue
                        external_sources = {
                            reference.source_id
                            for reference in asset.source_refs
                            if reference.source_id != source["source_id"]
                        }
                        if not external_sources.issubset(migrated_source_ids):
                            blocked = True
                            break
                        owned_assets.append((asset, asset.source_refs))
                    if blocked:
                        deferred.append(source)
                        continue
                    fragments = self.connection.execute(
                        "SELECT * FROM source_fragments WHERE source_id = ? ORDER BY ordinal",
                        (source["source_id"],),
                    ).fetchall()
                    assets_by_fragment: dict[str, list[KnowledgeAsset]] = {}
                    for asset, references in owned_assets:
                        for reference in references:
                            if reference.source_id == source["source_id"]:
                                assets_by_fragment.setdefault(reference.fragment_id, []).append(
                                    asset
                                )
                    source_ir_nodes: list[dict[str, Any]] = []
                    fragment_records: list[dict[str, Any]] = []
                    node_key_by_fragment: dict[str, str] = {}
                    for fragment in fragments:
                        candidates = assets_by_fragment.get(fragment["fragment_id"], [])
                        commitment = (
                            candidates[0].semantic_key
                            if candidates and candidates[0].semantic_key
                            else f"{fragment['locator']}:{fragment['ordinal']}"
                        )
                        logical_node_key = (
                            "legacy:"
                            + sha256_bytes(commitment.encode("utf-8"))[:32]
                            + f":{fragment['ordinal']}"
                        )
                        node_key_by_fragment[fragment["fragment_id"]] = logical_node_key
                        source_ir_nodes.append(
                            {
                                "logical_node_key": logical_node_key,
                                "parent_logical_node_key": None,
                                "ordinal": fragment["ordinal"],
                                "node_type": "legacy-fragment",
                                "title": candidates[0].title if candidates else source["title"],
                                "text": fragment["text"],
                                "locator": fragment["locator"],
                                "source_span": {"locator": fragment["locator"]},
                                "content_sha256": fragment["text_sha256"],
                                "quality_flags": ["migrated-v0.6-fragment"],
                                "instruction_risk": bool(fragment["instruction_risk"]),
                                "fragment_id": fragment["fragment_id"],
                            }
                        )
                        fragment_records.append(
                            {
                                "fragment_id": fragment["fragment_id"],
                                "ordinal": fragment["ordinal"],
                                "locator": fragment["locator"],
                                "text_sha256": fragment["text_sha256"],
                                "instruction_risk": bool(fragment["instruction_risk"]),
                                "logical_node_keys": [logical_node_key],
                            }
                        )
                    collection_id = make_collection_id(
                        vault_id=self.vault_id,
                        name="project",
                    )
                    logical_path = path_by_legacy_key[source["legacy_source_key"]]
                    canonical_source_key = make_source_key(
                        collection_id=collection_id,
                        logical_path=logical_path,
                    )
                    proposals: list[dict[str, Any]] = []
                    for asset, references in owned_assets:
                        local_node_keys = [
                            node_key_by_fragment[reference.fragment_id]
                            for reference in references
                            if reference.source_id == source["source_id"]
                        ]
                        knowledge_key = make_knowledge_key(
                            vault_id=self.vault_id,
                            source_key=canonical_source_key,
                            logical_node_key="+".join(local_node_keys),
                            proposal_role=asset.kind,
                        )
                        predecessor_revision_ids: list[str] = []
                        if asset.supersedes_asset_id is not None:
                            predecessor = self.connection.execute(
                                "SELECT asset_revision_id FROM asset_revision_bindings_v2 "
                                "WHERE legacy_asset_id = ?",
                                (asset.supersedes_asset_id,),
                            ).fetchone()
                            if predecessor is not None:
                                predecessor_revision_ids.append(predecessor[0])
                        knowledge_content_sha256 = sha256_bytes(
                            canonical_json(
                                {
                                    "kind": asset.kind,
                                    "memory_tier": asset.memory_tier,
                                    "title": asset.title,
                                    "statement": asset.statement,
                                    "knowledge_key": knowledge_key,
                                    "logical_node_keys": local_node_keys,
                                    "source_refs": [
                                        {
                                            "locator": reference.locator,
                                            "quote_sha256": reference.quote_sha256,
                                        }
                                        for reference in references
                                    ],
                                }
                            ).encode("utf-8")
                        )
                        proposals.append(
                            {
                                "legacy_asset_id": asset.asset_id,
                                "knowledge_key": knowledge_key,
                                "knowledge_content_sha256": knowledge_content_sha256,
                                "kind": asset.kind,
                                "title": asset.title,
                                "source_refs": [reference.to_dict() for reference in references],
                                "lineage_status": (
                                    "modified" if predecessor_revision_ids else "new"
                                ),
                                "predecessor_revision_ids": predecessor_revision_ids,
                                "logical_node_keys": local_node_keys,
                                "mapping_evidence": {
                                    "method": "v0.6-additive-migration",
                                    "legacy_semantic_key": asset.semantic_key,
                                },
                                "applicability": {},
                                "observed_at": asset.created_at,
                                "valid_from": None,
                                "valid_to": None,
                                "expires_at": asset.expires_at,
                                "project_scope": None,
                                "repository_scope": None,
                                "branch_scope": None,
                                "version_scope": None,
                                "environment_scope": None,
                                "warnings": list(asset.warnings),
                                "trust": asset.trust,
                                "sensitivity": asset.sensitivity,
                                "status": asset.status,
                            }
                        )
                    compiler = strict_json_loads(source["compiler_json"])
                    identity = register_compilation_identity(
                        self.connection,
                        vault_id=self.vault_id,
                        collection_id=collection_id,
                        collection_name="project",
                        logical_path=logical_path,
                        source_key=canonical_source_key,
                        legacy_source_id=source["source_id"],
                        content_sha256=source["content_sha256"],
                        media_identity=source["media_type"],
                        origin_uri=source["origin_uri"],
                        byte_size=source["byte_size"],
                        observed_at=source["imported_at"],
                        adapter="deeplaw-v0.6-migration",
                        adapter_version=compiler.get("schema_version", "unknown"),
                        configuration={
                            "legacy_compiler_sha256": sha256_bytes(
                                canonical_json(compiler).encode("utf-8")
                            ),
                            "migration_projection": "fragment-preserving/v1",
                        },
                        source_ir_nodes=source_ir_nodes,
                        fragments=fragment_records,
                        extractor="deeplaw-v0.6-migration",
                        extractor_revision="1",
                        model_identity=None,
                        prompt_configuration={"mode": "migration"},
                        proposals=proposals,
                        source_trust=source["trust"],
                        source_sensitivity=source["sensitivity"],
                    )
                    for asset, _ in owned_assets:
                        if asset.status == "active":
                            asset_identity = next(
                                item
                                for item in identity["asset_identities"]
                                if item["legacy_asset_id"] == asset.asset_id
                            )
                            record_governance_revision(
                                self.connection,
                                subject_kind="asset_revision",
                                subject_id=asset_identity["asset_revision_id"],
                                trust=asset.trust,
                                sensitivity=asset.sensitivity,
                                policy_id="deeplaw.v0.6-migration/v1",
                                review_status="human_verified",
                                lifecycle_status="active",
                                activation_status="active",
                                export_allowed=asset.sensitivity == "public",
                                reviewer_id="migration-audit-replay",
                                recorded_at=asset.activated_at or asset.created_at,
                            )
                        migrated_asset_ids.add(asset.asset_id)
                    if source["lifecycle_status"] == "active":
                        record_governance_revision(
                            self.connection,
                            subject_kind="source_revision",
                            subject_id=identity["source_revision_id"],
                            trust=source["trust"],
                            sensitivity=source["sensitivity"],
                            policy_id="deeplaw.v0.6-migration/v1",
                            review_status="human_verified",
                            lifecycle_status="active",
                            activation_status="active",
                            export_allowed=source["sensitivity"] == "public",
                            reviewer_id="migration-audit-replay",
                            recorded_at=source["imported_at"],
                        )
                    migrated_source_ids.add(source["source_id"])
                    identity_records.append(identity)
                    made_progress = True
                if not made_progress:
                    raise RuntimeError(
                        "Identity v2 migration found a cyclic or unresolved cross-source "
                        "proposal reference graph"
                    )
                pending = deferred
            expected_source_bound_assets = self.connection.execute(
                "SELECT COUNT(*) FROM assets WHERE json_array_length(source_refs_json) > 0"
            ).fetchone()[0]
            if len(migrated_asset_ids) != expected_source_bound_assets:
                raise RuntimeError("Identity v2 migration did not bind every source-backed asset")
            search_index = self._rebuild_search_index_in_transaction()
            self._append_event(
                event_type="search_index_rebuilt",
                object_id=None,
                payload=search_index,
            )
            revision, audit_head = self._append_identity_snapshot(
                reason="identity_migrated",
                source_revision_id=None,
            )
            migration_sha256 = sha256_bytes(canonical_json(identity_records).encode("utf-8"))
            self.connection.commit()
        except BaseException:
            self.connection.rollback()
            self.identity_v2_enabled = False
            raise
        verification = self.verify_identity_v2_migration()
        if not verification["valid"]:
            raise RuntimeError(
                "Identity v2 migration committed but failed verification; "
                f"restore the verified backup at {backup['backup_path']}"
            )
        return {
            **plan,
            "required": True,
            "applied": True,
            "backup": backup,
            "verification": verification,
            "revision": revision,
            "audit_head": audit_head,
            "migration_sha256": migration_sha256,
            "search_index": search_index,
        }

    def _rebuild_search_index_in_transaction(self) -> dict[str, Any]:
        self.connection.execute("DELETE FROM asset_search")
        inventory: list[dict[str, str]] = []
        for row in self.connection.execute("SELECT * FROM assets ORDER BY asset_id"):
            asset = self._row_to_asset(row)
            tokens = {
                "title_tokens": _token_string(asset.title),
                "statement_tokens": _token_string(asset.statement),
                "semantic_tokens": _token_string(asset.semantic_key or ""),
                "tag_tokens": _token_string(" ".join(asset.tags)),
            }
            self.connection.execute(
                "INSERT INTO asset_search VALUES (?, ?, ?, ?, ?)",
                (
                    asset.asset_id,
                    tokens["title_tokens"],
                    tokens["statement_tokens"],
                    tokens["semantic_tokens"],
                    tokens["tag_tokens"],
                ),
            )
            inventory.append({"asset_id": asset.asset_id, **tokens})
        return {
            "tokenizer_profile": "deeplaw-mixed-cjk-code/2",
            "asset_count": len(inventory),
            "inventory_sha256": sha256_bytes(canonical_json(inventory).encode("utf-8")),
        }

    def rebuild_derived_indexes(self) -> dict[str, Any]:
        """Rebuild removable lexical state without changing canonical Assets."""
        self._require_write()
        try:
            self.connection.execute("BEGIN IMMEDIATE")
            self._require_healthy_integrity()
            result = self._rebuild_search_index_in_transaction()
            revision, audit_head = self._append_event(
                event_type="search_index_rebuilt",
                object_id=None,
                payload=result,
            )
            self.connection.commit()
        except BaseException:
            self.connection.rollback()
            raise
        verification = self.verify_integrity()
        if not verification["valid"]:
            raise RuntimeError("rebuilt search index failed integrity verification")
        return {
            "schema_version": "deeplaw.knowledge-index-rebuild/v1",
            "vault_id": self.vault_id,
            **result,
            "revision": revision,
            "audit_head": audit_head,
            "valid": True,
        }

    def _require_healthy_integrity(self) -> None:
        integrity = self.verify_integrity()
        if not integrity["valid"]:
            raise RuntimeError("knowledge vault integrity is invalid; persistent operation stopped")

    def _database_file_fingerprint(self) -> tuple[int, int, int, int, int]:
        stat_result = self.database.stat()
        return (
            stat_result.st_dev,
            stat_result.st_ino,
            stat_result.st_size,
            stat_result.st_mtime_ns,
            stat_result.st_ctime_ns,
        )

    def _append_event(
        self,
        *,
        event_type: str,
        object_id: str | None,
        payload: dict[str, Any],
    ) -> tuple[int, str]:
        self._require_write()
        payload_json = canonical_json(payload)
        if len(payload_json.encode("utf-8")) > _MAX_EVENT_PAYLOAD_BYTES:
            raise ValueError("knowledge event payload exceeds the bound")
        current_revision = self.revision
        sequence = current_revision + 1
        previous_hash = self.audit_head
        created_at = utc_now()
        event = {
            "schema_version": KNOWLEDGE_EVENT_SCHEMA,
            "sequence": sequence,
            "event_type": event_type,
            "object_id": object_id,
            "payload": payload,
            "previous_hash": previous_hash,
            "created_at": created_at,
        }
        event_hash = sha256_bytes(canonical_json(event).encode("utf-8"))
        self.connection.execute(
            "INSERT INTO events VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (
                sequence,
                KNOWLEDGE_EVENT_SCHEMA,
                event_type,
                object_id,
                payload_json,
                previous_hash,
                event_hash,
                created_at,
            ),
        )
        self.connection.execute(
            "UPDATE metadata SET value = ? WHERE key = 'revision'",
            (str(sequence),),
        )
        self.connection.execute(
            "UPDATE metadata SET value = ? WHERE key = 'audit_head'",
            (event_hash,),
        )
        return sequence, event_hash

    def _append_identity_snapshot(
        self,
        *,
        reason: str,
        source_revision_id: str | None,
    ) -> tuple[int, str]:
        if not self.identity_v2_enabled:
            return self.revision, self.audit_head
        snapshot = identity_snapshot(self.connection)
        return self._append_event(
            event_type="identity_v2_snapshot",
            object_id=self.vault_id,
            payload={
                "identity_root_sha256": snapshot["identity_root_sha256"],
                "reason": reason,
                "source_revision_id": source_revision_id,
            },
        )

    def source_by_hash(self, content_sha256: str) -> dict[str, Any] | None:
        if not _SHA256.fullmatch(content_sha256):
            raise ValueError("source hash must be lowercase SHA-256")
        row = self.connection.execute(
            "SELECT * FROM sources WHERE content_sha256 = ? ORDER BY source_id LIMIT 1",
            (content_sha256,),
        ).fetchone()
        return self._source_row(row) if row is not None else None

    def _source_by_identity(
        self,
        *,
        source_key: str,
        content_sha256: str,
        source_kind: SourceKind,
        title: str,
        origin_uri: str | None,
        trust: TrustLevel,
        sensitivity: Sensitivity,
        compiler: dict[str, Any],
    ) -> dict[str, Any] | None:
        source_id = stable_id(
            "source",
            self.vault_id,
            source_kind,
            content_sha256,
            title,
            origin_uri or "",
            trust,
            sensitivity,
            canonical_json(compiler),
        )
        row = self.connection.execute(
            "SELECT * FROM sources WHERE source_id = ?",
            (source_id,),
        ).fetchone()
        return self._source_row(row) if row is not None else None

    @staticmethod
    def _source_row(row: sqlite3.Row) -> dict[str, Any]:
        return {
            "source_id": row["source_id"],
            "kind": row["kind"],
            "title": row["title"],
            "origin_uri": row["origin_uri"],
            "stored_name": row["stored_name"],
            "media_type": row["media_type"],
            "byte_size": row["byte_size"],
            "content_sha256": row["content_sha256"],
            "trust": row["trust"],
            "sensitivity": row["sensitivity"],
            "imported_at": row["imported_at"],
            "instruction_risk": bool(row["instruction_risk"]),
            "warnings": strict_json_loads(row["warnings_json"]),
            "compiler": strict_json_loads(row["compiler_json"]),
        }

    def source_info(self, source_id: str) -> dict[str, Any]:
        if not isinstance(source_id, str) or not _SOURCE_ID.fullmatch(source_id):
            raise ValueError("knowledge source ID is invalid")
        row = self.connection.execute(
            "SELECT * FROM sources WHERE source_id = ?",
            (source_id,),
        ).fetchone()
        if row is None:
            raise KeyError(f"knowledge source is unavailable: {source_id}")
        source = self._source_row(row)
        if not self.control_enabled:
            return {
                **source,
                "source_key": None,
                "canonical_source_key": None,
                "previous_source_id": None,
                "status": "legacy",
                "activated_at": None,
                "superseded_at": None,
                "removed_at": None,
            }
        lifecycle = self.connection.execute(
            "SELECT * FROM source_lifecycle WHERE source_id = ?",
            (source_id,),
        ).fetchone()
        if lifecycle is None:
            raise RuntimeError("knowledge source lifecycle is missing")
        identity: dict[str, Any] = {
            "canonical_source_key": None,
            "source_revision_id": None,
            "compilation_id": None,
            "proposal_set_id": None,
            "collection_id": None,
            "logical_path": None,
            "governance_revision": None,
            "governance": None,
        }
        if self.identity_v2_enabled:
            identity_row = self.connection.execute(
                """
                SELECT source_revisions_v2.source_key AS canonical_source_key,
                       source_revisions_v2.source_revision_id,
                       compilations_v2.compilation_id,
                       proposal_sets_v2.proposal_set_id,
                       source_locations_v2.collection_id,
                       source_locations_v2.logical_path
                FROM source_revision_bindings_v2
                JOIN source_revisions_v2 USING(source_revision_id)
                JOIN source_build_bindings_v2 USING(legacy_source_id)
                JOIN compilations_v2 USING(compilation_id)
                JOIN proposal_sets_v2 USING(proposal_set_id)
                JOIN source_locations_v2
                  ON source_locations_v2.legacy_source_id =
                     source_revision_bindings_v2.legacy_source_id
                WHERE source_revision_bindings_v2.legacy_source_id = ?
                ORDER BY source_build_bindings_v2.observed_at DESC
                LIMIT 1
                """,
                (source_id,),
            ).fetchone()
            if identity_row is not None:
                governance = self.connection.execute(
                    """
                    SELECT *
                    FROM governance_revisions_v2
                    WHERE subject_kind = 'source_revision' AND subject_id = ?
                    ORDER BY recorded_at DESC,
                             CASE
                                 WHEN activation_status = 'inactive'
                                  AND lifecycle_status NOT IN (
                                      'pending', 'proposed', 'quarantined'
                                  ) THEN 2
                                 WHEN activation_status = 'active' THEN 1
                                 ELSE 0
                             END DESC,
                             governance_revision DESC
                    LIMIT 1
                    """,
                    (identity_row["source_revision_id"],),
                ).fetchone()
                identity = {
                    "canonical_source_key": identity_row["canonical_source_key"],
                    "source_revision_id": identity_row["source_revision_id"],
                    "compilation_id": identity_row["compilation_id"],
                    "proposal_set_id": identity_row["proposal_set_id"],
                    "collection_id": identity_row["collection_id"],
                    "logical_path": identity_row["logical_path"],
                    "governance_revision": (
                        governance["governance_revision"] if governance is not None else None
                    ),
                    "governance": (
                        {
                            "trust": governance["trust"],
                            "sensitivity": governance["sensitivity"],
                            "policy_id": governance["policy_id"],
                            "review_status": governance["review_status"],
                            "lifecycle_status": governance["lifecycle_status"],
                            "activation_status": governance["activation_status"],
                            "revoked_at": governance["revoked_at"],
                            "export_allowed": bool(governance["export_allowed"]),
                            "reviewer_id": governance["reviewer_id"],
                            "recorded_at": governance["recorded_at"],
                        }
                        if governance is not None
                        else None
                    ),
                }
        return {
            **source,
            "source_key": lifecycle["source_key"],
            **identity,
            "previous_source_id": lifecycle["previous_source_id"],
            "status": lifecycle["status"],
            "activated_at": lifecycle["activated_at"],
            "superseded_at": lifecycle["superseded_at"],
            "removed_at": lifecycle["removed_at"],
        }

    def active_source_for_key(self, source_key: str) -> dict[str, Any] | None:
        self._require_control()
        if not isinstance(source_key, str) or not _SOURCE_KEY.fullmatch(source_key):
            raise ValueError("knowledge source key is invalid")
        row = self.connection.execute(
            """
            SELECT source_id FROM source_lifecycle
            WHERE source_key = ? AND status = 'active'
            """,
            (source_key,),
        ).fetchone()
        return self.source_info(row["source_id"]) if row is not None else None

    def update_source_governance(
        self,
        source_id: str,
        *,
        trust: TrustLevel,
        sensitivity: Sensitivity,
        export_allowed: bool,
        reviewer_id: str,
        reason: str,
        confirm_reviewed: bool,
    ) -> dict[str, Any]:
        """Record policy independently from immutable source identity."""
        self._require_write()
        self._require_control()
        if not self.identity_v2_enabled:
            raise RuntimeError("Knowledge Identity v2 is required for governance changes")
        if not confirm_reviewed:
            raise ValueError("source governance change requires explicit operator review")
        if trust not in TRUST_LEVELS or trust == "verified_source":
            raise ValueError("source governance trust is invalid")
        if sensitivity not in SENSITIVITY_LEVELS:
            raise ValueError("source governance sensitivity is invalid")
        reviewer_id = reviewer_id.strip()
        reason = reason.strip()
        if not 1 <= len(reviewer_id) <= 200 or not 1 <= len(reason) <= 2_000:
            raise ValueError("source governance reviewer or reason is invalid")
        try:
            self.connection.execute("BEGIN IMMEDIATE")
            self._require_healthy_integrity()
            source = self.source_info(source_id)
            source_revision_id = source.get("source_revision_id")
            if source_revision_id is None:
                raise RuntimeError("source revision identity is unavailable")
            prior = self.connection.execute(
                """
                SELECT * FROM governance_revisions_v2
                WHERE subject_kind = 'source_revision' AND subject_id = ?
                ORDER BY recorded_at DESC, governance_revision DESC LIMIT 1
                """,
                (source_revision_id,),
            ).fetchone()
            if prior is None:
                raise RuntimeError("source governance history is unavailable")
            recorded_at = utc_now()
            if recorded_at <= prior["recorded_at"]:
                prior_time = datetime.fromisoformat(prior["recorded_at"].replace("Z", "+00:00"))
                recorded_at = (
                    (prior_time + timedelta(seconds=1))
                    .astimezone(UTC)
                    .isoformat(timespec="seconds")
                    .replace("+00:00", "Z")
                )
            governance_revision = record_governance_revision(
                self.connection,
                subject_kind="source_revision",
                subject_id=source_revision_id,
                trust=trust,
                sensitivity=sensitivity,
                policy_id="deeplaw.local-source-governance/v2",
                review_status=prior["review_status"],
                lifecycle_status=prior["lifecycle_status"],
                activation_status=prior["activation_status"],
                revoked_at=prior["revoked_at"],
                export_allowed=export_allowed,
                reviewer_id=reviewer_id,
                recorded_at=recorded_at,
            )
            revision, audit_head = self._append_identity_snapshot(
                reason="governance_recorded",
                source_revision_id=source_revision_id,
            )
            self.connection.commit()
        except BaseException:
            self.connection.rollback()
            raise
        return {
            "schema_version": "deeplaw.source-governance-change/v1",
            "vault_id": self.vault_id,
            "source_id": source_id,
            "source_revision_id": source_revision_id,
            "previous_governance_revision": prior["governance_revision"],
            "governance_revision": governance_revision,
            "trust": trust,
            "sensitivity": sensitivity,
            "export_allowed": export_allowed,
            "reviewer_id": reviewer_id,
            "reason": reason,
            "recorded_at": recorded_at,
            "revision": revision,
            "audit_head": audit_head,
            "source_revision_changed": False,
        }

    @staticmethod
    def _source_ir_row(row: sqlite3.Row, *, include_text: bool) -> dict[str, Any]:
        result = {
            "node_id": row["node_id"],
            "compilation_id": row["compilation_id"],
            "source_revision_id": row["source_revision_id"],
            "logical_node_key": row["logical_node_key"],
            "parent_node_id": row["parent_node_id"],
            "ordinal": row["ordinal"],
            "node_type": row["node_type"],
            "title": row["title"],
            "locator": row["locator"],
            "source_span": strict_json_loads(row["source_span_json"]),
            "content_sha256": row["content_sha256"],
            "adapter": row["adapter"],
            "adapter_version": row["adapter_version"],
            "quality_flags": strict_json_loads(row["quality_flags_json"]),
            "instruction_risk": bool(row["instruction_risk"]),
        }
        if include_text:
            result["text"] = row["text"]
        return result

    def structure_get(self, node_id: str, *, max_chars: int = 20_000) -> dict[str, Any]:
        if not self.identity_v2_enabled:
            raise RuntimeError("Knowledge Identity v2 is not installed")
        if not isinstance(node_id, str) or not re.fullmatch(r"irnode_[0-9a-f]{24}", node_id):
            raise ValueError("Source IR node ID is invalid")
        if isinstance(max_chars, bool) or not 1 <= max_chars <= 20_000:
            raise ValueError("Source IR max_chars must be between 1 and 20000")
        row = self.connection.execute(
            "SELECT * FROM source_ir_nodes_v2 WHERE node_id = ?",
            (node_id,),
        ).fetchone()
        if row is None:
            raise KeyError(f"Source IR node is unavailable: {node_id}")
        result = self._source_ir_row(row, include_text=True)
        text = result["text"]
        result["text"] = text[:max_chars]
        result["text_truncated"] = len(text) > max_chars
        result["trace"] = self.structure_trace(node_id)["nodes"]
        return {
            "schema_version": "deeplaw.source-structure/v1",
            "vault_id": self.vault_id,
            "node": result,
        }

    def structure_list(
        self,
        *,
        source_id: str | None = None,
        compilation_id: str | None = None,
        parent_node_id: str | None = None,
        limit: int = 100,
    ) -> dict[str, Any]:
        if not self.identity_v2_enabled:
            raise RuntimeError("Knowledge Identity v2 is not installed")
        if isinstance(limit, bool) or not 1 <= limit <= 500:
            raise ValueError("Source structure limit must be between 1 and 500")
        if source_id is not None:
            source = self.source_info(source_id)
            compilation_id = source.get("compilation_id")
        if compilation_id is None:
            raise ValueError("Source structure list requires a source or compilation ID")
        if not re.fullmatch(r"compilation_[0-9a-f]{24}", compilation_id):
            raise ValueError("compilation ID is invalid")
        if parent_node_id is None:
            predicate = "parent_node_id IS NULL"
            parameters: tuple[Any, ...] = (compilation_id, limit + 1)
        else:
            if not re.fullmatch(r"irnode_[0-9a-f]{24}", parent_node_id):
                raise ValueError("Source IR parent node ID is invalid")
            predicate = "parent_node_id = ?"
            parameters = (compilation_id, parent_node_id, limit + 1)
        rows = self.connection.execute(
            f"""
            SELECT * FROM source_ir_nodes_v2
            WHERE compilation_id = ? AND {predicate}
            ORDER BY ordinal, node_id LIMIT ?
            """,
            parameters,
        ).fetchall()
        return {
            "schema_version": "deeplaw.source-structure-list/v1",
            "vault_id": self.vault_id,
            "source_id": source_id,
            "compilation_id": compilation_id,
            "parent_node_id": parent_node_id,
            "nodes": [self._source_ir_row(row, include_text=False) for row in rows[:limit]],
            "truncated": len(rows) > limit,
        }

    def structure_search(
        self,
        query: str,
        *,
        source_id: str | None = None,
        limit: int = 20,
    ) -> dict[str, Any]:
        if not self.identity_v2_enabled:
            raise RuntimeError("Knowledge Identity v2 is not installed")
        terms = search_terms(query)
        if not terms:
            raise ValueError("Source structure query produced no searchable terms")
        if isinstance(limit, bool) or not 1 <= limit <= 100:
            raise ValueError("Source structure search limit must be between 1 and 100")
        compilation_id: str | None = None
        if source_id is not None:
            compilation_id = self.source_info(source_id).get("compilation_id")
        rows = self.connection.execute(
            """
            SELECT * FROM source_ir_nodes_v2
            WHERE (? IS NULL OR compilation_id = ?)
            ORDER BY compilation_id, ordinal
            LIMIT 10000
            """,
            (compilation_id, compilation_id),
        ).fetchall()
        scored: list[tuple[int, sqlite3.Row]] = []
        folded_terms = tuple(term.casefold() for term in terms[:32])
        for row in rows:
            folded_title = (row["title"] or "").casefold()
            haystack = f"{folded_title}\n{row['text'].casefold()}"
            score = sum(
                3 if term in folded_title else 1 for term in folded_terms if term in haystack
            )
            if score:
                scored.append((score, row))
        scored.sort(key=lambda item: (-item[0], item[1]["ordinal"], item[1]["node_id"]))
        selected = scored[:limit]
        return {
            "schema_version": "deeplaw.source-structure-search/v1",
            "vault_id": self.vault_id,
            "query": query,
            "search_terms": list(folded_terms),
            "results": [
                {
                    **self._source_ir_row(row, include_text=False),
                    "match_score": score,
                }
                for score, row in selected
            ],
            "truncated": len(scored) > limit,
            "verification_boundary": (
                "Tree candidates must be verified against Source IR and stored evidence."
            ),
        }

    def structure_trace(self, node_id: str) -> dict[str, Any]:
        if not self.identity_v2_enabled:
            raise RuntimeError("Knowledge Identity v2 is not installed")
        if not isinstance(node_id, str) or not re.fullmatch(r"irnode_[0-9a-f]{24}", node_id):
            raise ValueError("Source IR node ID is invalid")
        nodes: list[dict[str, Any]] = []
        current = node_id
        seen: set[str] = set()
        for _ in range(64):
            if current in seen:
                raise RuntimeError("Source IR hierarchy contains a cycle")
            seen.add(current)
            row = self.connection.execute(
                "SELECT * FROM source_ir_nodes_v2 WHERE node_id = ?",
                (current,),
            ).fetchone()
            if row is None:
                raise KeyError(f"Source IR node is unavailable: {current}")
            nodes.append(self._source_ir_row(row, include_text=False))
            if row["parent_node_id"] is None:
                break
            current = row["parent_node_id"]
        else:
            raise RuntimeError("Source IR hierarchy exceeds the trace bound")
        nodes.reverse()
        return {
            "schema_version": "deeplaw.source-structure-trace/v1",
            "vault_id": self.vault_id,
            "node_id": node_id,
            "nodes": nodes,
        }

    def active_asset_for_semantic_key(self, semantic_key: str) -> KnowledgeAsset | None:
        row = self.connection.execute(
            """
            SELECT * FROM assets
            WHERE semantic_key = ? AND status = 'active'
            """,
            (semantic_key,),
        ).fetchone()
        return self._row_to_asset(row) if row is not None else None

    def add_compiled_source(
        self,
        *,
        source_path: Path,
        source_key: str,
        expected_byte_size: int,
        expected_content_sha256: str,
        source_kind: SourceKind,
        title: str,
        origin_uri: str | None,
        media_type: str,
        trust: TrustLevel,
        sensitivity: Sensitivity,
        instruction_risk: bool,
        warnings: tuple[str, ...],
        compiler: dict[str, Any],
        fragments: tuple[dict[str, Any], ...],
        asset_specs: tuple[dict[str, Any], ...],
        source_ir_nodes: tuple[dict[str, Any], ...] | None = None,
    ) -> dict[str, Any]:
        self._require_write()
        self._require_control()
        self._require_healthy_integrity()
        if source_kind not in SOURCE_KINDS:
            raise ValueError("unsupported knowledge source kind")
        if not isinstance(source_key, str) or not _SOURCE_KEY.fullmatch(source_key):
            raise ValueError("knowledge source key is invalid")
        if trust == "verified_source":
            raise ValueError(
                "verified_source is reserved for a future publisher-verification "
                "workflow; use user_provided or untrusted"
            )
        if trust not in TRUST_LEVELS or sensitivity not in SENSITIVITY_LEVELS:
            raise ValueError("unsupported knowledge source trust or sensitivity")
        if (
            not isinstance(title, str)
            or not title.strip()
            or title != title.strip()
            or len(title) > 500
        ):
            raise ValueError("knowledge source title is invalid")
        if origin_uri is not None and (
            not isinstance(origin_uri, str)
            or not origin_uri.strip()
            or origin_uri != origin_uri.strip()
            or len(origin_uri) > 2_000
        ):
            raise ValueError("knowledge source origin URI is invalid")
        if (
            not isinstance(media_type, str)
            or not media_type.strip()
            or media_type != media_type.strip()
            or len(media_type) > 200
        ):
            raise ValueError("knowledge source media type is invalid")
        if (
            not isinstance(compiler, dict)
            or compiler.get("schema_version") != "deeplaw.knowledge-compiler/v1"
            or compiler.get("source_key") != source_key
        ):
            raise ValueError("knowledge source compiler identity is invalid")
        try:
            compiler_json = canonical_json(compiler)
        except (TypeError, ValueError) as error:
            raise ValueError("knowledge source compiler identity is not serializable") from error
        if len(compiler_json.encode("utf-8")) > _MAX_COMPILER_BYTES:
            raise ValueError("knowledge source compiler identity exceeds the bound")
        if len(warnings) > 64 or any(
            not isinstance(warning, str)
            or not warning.strip()
            or warning != warning.strip()
            or len(warning) > 500
            for warning in warnings
        ):
            raise ValueError("knowledge source warnings are invalid")
        source_input = source_path.expanduser().absolute()
        if source_input.is_symlink():
            raise ValueError("knowledge source must be a regular non-symlink file")
        source = source_input.resolve(strict=True)
        if not source.is_file():
            raise ValueError("knowledge source must be a regular file")
        byte_size = source.stat().st_size
        if not 1 <= byte_size <= _MAX_SOURCE_BYTES:
            raise ValueError("knowledge source is empty or exceeds 512 MiB")
        content_sha256 = sha256_file(source)
        if (
            isinstance(expected_byte_size, bool)
            or expected_byte_size != byte_size
            or not isinstance(expected_content_sha256, str)
            or not _SHA256.fullmatch(expected_content_sha256)
            or expected_content_sha256 != content_sha256
        ):
            raise RuntimeError("knowledge source changed while it was being compiled")
        existing = self._source_by_identity(
            source_key=source_key,
            content_sha256=content_sha256,
            source_kind=source_kind,
            title=title,
            origin_uri=origin_uri,
            trust=trust,
            sensitivity=sensitivity,
            compiler=compiler,
        )
        if existing is not None:
            existing_path = self.source_file_path(existing["source_id"])
            if (
                existing_path.is_symlink()
                or not existing_path.is_file()
                or existing_path.stat().st_size != existing["byte_size"]
                or sha256_file(existing_path) != existing["content_sha256"]
            ):
                raise RuntimeError("existing knowledge source failed its content-integrity check")
            asset_rows = self.connection.execute(
                """
                SELECT DISTINCT assets.asset_id
                FROM assets, json_each(assets.source_refs_json) AS reference
                WHERE json_extract(reference.value, '$.source_id') = ?
                ORDER BY assets.asset_id
                """,
                (existing["source_id"],),
            ).fetchall()
            return {
                "schema_version": "deeplaw.knowledge-ingest/v1",
                "vault_id": self.vault_id,
                "revision": self.revision,
                "source": self.source_info(existing["source_id"]),
                "asset_ids": [row["asset_id"] for row in asset_rows],
                "idempotent": True,
            }
        if not fragments or len(fragments) > _MAX_FRAGMENTS_PER_SOURCE:
            raise ValueError("knowledge source must produce a bounded non-empty fragment set")
        source_id = stable_id(
            "source",
            self.vault_id,
            source_kind,
            content_sha256,
            title,
            origin_uri or "",
            trust,
            sensitivity,
            canonical_json(compiler),
        )
        suffix = source.suffix.lower()[:16]
        stored_name = f"{content_sha256}{suffix}"
        destination = self.root / "sources" / stored_name
        if destination.is_symlink():
            raise RuntimeError("knowledge source destination must not be a symbolic link")
        if not destination.exists():
            temporary = destination.with_name(f".{destination.name}.{secrets.token_hex(8)}.tmp")
            try:
                shutil.copyfile(source, temporary)
                if (
                    temporary.stat().st_size != byte_size
                    or sha256_file(temporary) != content_sha256
                ):
                    raise RuntimeError("copied knowledge source failed its integrity check")
                os.chmod(temporary, 0o600)
                os.replace(temporary, destination)
            except BaseException:
                temporary.unlink(missing_ok=True)
                raise
        elif (
            not destination.is_file()
            or destination.stat().st_size != byte_size
            or sha256_file(destination) != content_sha256
        ):
            raise RuntimeError("existing knowledge source file does not match its content identity")
        else:
            os.chmod(destination, 0o600)
        imported_at = utc_now()
        previous_source = self.active_source_for_key(source_key)
        previous_source_id = previous_source["source_id"] if previous_source is not None else None
        asset_ids: list[str] = []
        fragment_ids: list[str] = []
        try:
            self.connection.execute("BEGIN IMMEDIATE")
            self._require_healthy_integrity()
            self.connection.execute(
                "INSERT INTO sources VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    source_id,
                    source_kind,
                    title,
                    origin_uri,
                    stored_name,
                    media_type,
                    byte_size,
                    content_sha256,
                    trust,
                    sensitivity,
                    imported_at,
                    int(instruction_risk),
                    canonical_json(list(warnings)),
                    compiler_json,
                ),
            )
            self.connection.execute(
                """
                INSERT INTO source_lifecycle(
                    source_id, source_key, previous_source_id, status,
                    activated_at, superseded_at, removed_at
                ) VALUES (?, ?, ?, 'pending', NULL, NULL, NULL)
                """,
                (source_id, source_key, previous_source_id),
            )
            fragment_rows: list[dict[str, Any]] = []
            for ordinal, fragment in enumerate(fragments, start=1):
                text = fragment["text"]
                locator = fragment["locator"]
                if (
                    not isinstance(text, str)
                    or not text.strip()
                    or text != text.strip()
                    or len(text) > _MAX_FRAGMENT_CHARS
                ):
                    raise ValueError("compiled source fragment text is invalid")
                if (
                    not isinstance(locator, str)
                    or not locator.strip()
                    or locator != locator.strip()
                    or len(locator) > 2_000
                ):
                    raise ValueError("compiled source fragment locator is invalid")
                text_sha256 = sha256_bytes(text.encode("utf-8"))
                fragment_id = stable_id(
                    "fragment",
                    source_id,
                    str(ordinal),
                    locator,
                    text_sha256,
                )
                fragment_ids.append(fragment_id)
                self.connection.execute(
                    "INSERT INTO source_fragments VALUES (?, ?, ?, ?, ?, ?, ?)",
                    (
                        fragment_id,
                        source_id,
                        ordinal,
                        locator,
                        text,
                        text_sha256,
                        int(bool(fragment.get("instruction_risk", instruction_risk))),
                    ),
                )
                fragment_rows.append(
                    {
                        "fragment_id": fragment_id,
                        "ordinal": ordinal,
                        "locator": locator,
                        "text_sha256": text_sha256,
                        "instruction_risk": bool(
                            fragment.get("instruction_risk", instruction_risk)
                        ),
                        "logical_node_keys": [
                            *fragment.get(
                                "logical_node_keys",
                                (fragment.get("logical_node_key", f"fragment:{ordinal}"),),
                            )
                        ],
                    }
                )
            if len(asset_specs) > _MAX_BATCH_APPROVAL_ASSETS:
                raise ValueError("compiled proposal set exceeds the 100000-proposal bound")
            identity_proposals: list[dict[str, Any]] = []
            for specification_index, specification in enumerate(asset_specs):
                explicit_references = specification.get("source_refs")
                raw_indexes = specification.get("source_ref_indexes")
                if explicit_references is None and raw_indexes is None:
                    if len(asset_specs) != len(fragments):
                        raise ValueError(
                            "compiled proposals require an explicit source reference graph"
                        )
                    raw_indexes = (specification_index,)
                if explicit_references is not None:
                    if (
                        not isinstance(explicit_references, (list, tuple))
                        or not explicit_references
                        or len(explicit_references) > 100
                    ):
                        raise ValueError("compiled proposal source reference graph is invalid")
                    resolved_references: list[SourceReference] = []
                    for reference in explicit_references:
                        if not isinstance(reference, dict):
                            raise ValueError("compiled proposal source reference graph is invalid")
                        reference_source_id = reference.get("source_id")
                        reference_fragment_id = reference.get("fragment_id")
                        evidence = self.connection.execute(
                            """
                            SELECT source_id, fragment_id, locator, text_sha256
                            FROM source_fragments
                            WHERE source_id = ? AND fragment_id = ?
                            """,
                            (reference_source_id, reference_fragment_id),
                        ).fetchone()
                        if evidence is None:
                            raise ValueError(
                                "compiled proposal references unavailable source evidence"
                            )
                        resolved_references.append(
                            SourceReference(
                                source_id=evidence["source_id"],
                                fragment_id=evidence["fragment_id"],
                                locator=evidence["locator"],
                                quote_sha256=evidence["text_sha256"],
                            )
                        )
                    references = tuple(resolved_references)
                    raw_indexes = ()
                else:
                    if (
                        not isinstance(raw_indexes, (list, tuple))
                        or not raw_indexes
                        or len(raw_indexes) > 100
                        or len(set(raw_indexes)) != len(raw_indexes)
                        or any(
                            isinstance(index, bool)
                            or not isinstance(index, int)
                            or not 0 <= index < len(fragments)
                            for index in raw_indexes
                        )
                    ):
                        raise ValueError("compiled proposal source reference graph is invalid")
                    references = tuple(
                        SourceReference(
                            source_id=source_id,
                            fragment_id=fragment_ids[index],
                            locator=fragments[index]["locator"],
                            quote_sha256=sha256_bytes(fragments[index]["text"].encode("utf-8")),
                        )
                        for index in raw_indexes
                    )
                proposal_kind = cast(
                    AssetKind,
                    specification.get("kind", "reference"),
                )
                default_logical_node_keys = (
                    tuple(
                        key
                        for index in raw_indexes
                        for key in fragments[index].get(
                            "logical_node_keys",
                            (
                                fragments[index].get(
                                    "logical_node_key",
                                    f"fragment:{index + 1}",
                                ),
                            ),
                        )
                    )
                    if raw_indexes
                    else tuple(f"evidence:{reference.fragment_id}" for reference in references)
                )
                logical_node_keys = tuple(
                    specification.get(
                        "logical_node_keys",
                        default_logical_node_keys,
                    )
                )
                knowledge_key = specification.get("knowledge_key") or make_knowledge_key(
                    vault_id=self.vault_id,
                    source_key=source_key,
                    logical_node_key="+".join(logical_node_keys),
                    proposal_role=specification.get("proposal_role", proposal_kind),
                )
                supersedes_asset_id = specification.get("supersedes_asset_id")
                predecessors: list[str] = []
                lineage_status = "new"
                mapping_evidence: dict[str, Any] = {
                    "method": "logical-node-key",
                    "logical_node_keys": list(logical_node_keys),
                }
                if supersedes_asset_id is not None:
                    prior_asset = self.get_asset(
                        supersedes_asset_id,
                        include_inactive=True,
                    )
                    lineage_status = (
                        "unchanged"
                        if prior_asset.statement == specification["statement"]
                        and prior_asset.kind == proposal_kind
                        else "modified"
                    )
                    lineage_status_hint = specification.get("lineage_status_hint")
                    if lineage_status_hint in {"renamed", "moved"}:
                        if lineage_status != "unchanged":
                            raise ValueError(
                                "renamed or moved lineage requires unchanged knowledge content"
                            )
                        lineage_status = lineage_status_hint
                    prior_identity = self.connection.execute(
                        "SELECT asset_revision_id FROM asset_revision_bindings_v2 "
                        "WHERE legacy_asset_id = ?",
                        (supersedes_asset_id,),
                    ).fetchone()
                    if prior_identity is not None:
                        predecessors.append(prior_identity["asset_revision_id"])
                    mapping_evidence["predecessor_asset_id"] = supersedes_asset_id
                    if lineage_status in {"renamed", "moved"}:
                        mapping_evidence.update(
                            {
                                "method": "explicit-source-relocation",
                                "previous_logical_path": specification.get("previous_logical_path"),
                                "current_logical_path": specification.get("current_logical_path"),
                            }
                        )
                proposal_trust = specification.get("trust", trust)
                if proposal_trust not in TRUST_LEVELS or proposal_trust == "verified_source":
                    raise ValueError("compiled proposal trust is invalid")
                explicit_quarantine = specification.get("quarantined", False)
                if not isinstance(explicit_quarantine, bool):
                    raise ValueError("compiled proposal quarantine flag is invalid")
                proposal_instruction_risk = has_instruction_risk(
                    f"{specification['title']}\n{specification['statement']}"
                )
                proposal_quarantined = bool(
                    instruction_risk or explicit_quarantine or proposal_instruction_risk
                )
                asset, _ = self._insert_asset(
                    kind=proposal_kind,
                    memory_tier=cast(
                        MemoryTier,
                        specification.get("memory_tier", "domain"),
                    ),
                    title=specification["title"],
                    statement=specification["statement"],
                    semantic_key=knowledge_key,
                    status="quarantined" if proposal_quarantined else "proposed",
                    verification="source_bound",
                    trust=cast(TrustLevel, proposal_trust),
                    sensitivity=sensitivity,
                    source_refs=references,
                    tags=tuple(specification.get("tags", ())),
                    warnings=tuple(
                        dict.fromkeys(
                            (
                                *specification.get("warnings", ()),
                                *(
                                    ("source contains instruction-like content",)
                                    if instruction_risk
                                    else ()
                                ),
                                *(
                                    ("proposal contains instruction-like content",)
                                    if proposal_instruction_risk and not instruction_risk
                                    else ()
                                ),
                            )
                        )
                    ),
                    expires_at=specification.get("expires_at"),
                    supersedes_asset_id=supersedes_asset_id,
                    origin_uri=specification.get("origin_uri"),
                    created_at=imported_at,
                )
                asset_ids.append(asset.asset_id)
                knowledge_content_sha256 = sha256_bytes(
                    canonical_json(
                        {
                            "kind": asset.kind,
                            "memory_tier": asset.memory_tier,
                            "title": asset.title,
                            "statement": asset.statement,
                            "knowledge_key": knowledge_key,
                            "logical_node_keys": list(logical_node_keys),
                            "source_refs": [
                                {
                                    "locator": reference.locator,
                                    "quote_sha256": reference.quote_sha256,
                                }
                                for reference in references
                            ],
                        }
                    ).encode("utf-8")
                )
                identity_proposals.append(
                    {
                        "legacy_asset_id": asset.asset_id,
                        "knowledge_key": knowledge_key,
                        "knowledge_content_sha256": knowledge_content_sha256,
                        "kind": asset.kind,
                        "title": asset.title,
                        "source_refs": [reference.to_dict() for reference in references],
                        "lineage_status": lineage_status,
                        "predecessor_revision_ids": predecessors,
                        "logical_node_keys": list(logical_node_keys),
                        "mapping_evidence": mapping_evidence,
                        "applicability": specification.get("applicability", {}),
                        "observed_at": specification.get("observed_at", imported_at),
                        "valid_from": specification.get("valid_from"),
                        "valid_to": specification.get("valid_to"),
                        "expires_at": asset.expires_at,
                        "project_scope": specification.get("project_scope"),
                        "repository_scope": specification.get("repository_scope"),
                        "branch_scope": specification.get("branch_scope"),
                        "version_scope": specification.get("version_scope"),
                        "environment_scope": specification.get("environment_scope"),
                        "warnings": list(asset.warnings),
                        "trust": asset.trust,
                        "sensitivity": asset.sensitivity,
                        "status": asset.status,
                    }
                )
            fragment_titles = [
                fragment.get("title")
                or (asset_specs[index].get("title") if len(asset_specs) == len(fragments) else None)
                for index, fragment in enumerate(fragments)
            ]
            prepared_source_ir_nodes = (
                [dict(node) for node in source_ir_nodes]
                if source_ir_nodes is not None
                else [
                    {
                        "logical_node_key": fragment.get(
                            "logical_node_key",
                            f"fragment:{index + 1}",
                        ),
                        "parent_logical_node_key": fragment.get("parent_logical_node_key"),
                        "ordinal": index + 1,
                        "node_type": fragment.get("node_type", "text"),
                        "title": fragment_titles[index],
                        "text": fragment["text"],
                        "locator": fragment["locator"],
                        "source_span": fragment.get(
                            "source_span",
                            {"locator": fragment["locator"]},
                        ),
                        "content_sha256": sha256_bytes(fragment["text"].encode("utf-8")),
                        "quality_flags": list(fragment.get("quality_flags", ())),
                        "instruction_risk": bool(
                            fragment.get("instruction_risk", instruction_risk)
                        ),
                        "fragment_id": fragment_ids[index],
                    }
                    for index, fragment in enumerate(fragments)
                ]
            )
            identity_collection_id = compiler.get("collection_id") or make_collection_id(
                vault_id=self.vault_id,
                name="project",
            )
            identity_logical_path = compiler.get("logical_path")
            if identity_logical_path is None:
                identity_logical_path = (
                    f"origins/{sha256_bytes(canonical_origin_commitment(origin_uri).encode('utf-8'))}"
                    if origin_uri is not None
                    else normalize_logical_path(source.name)
                )
            typed_extractor = compiler.get("typed_extractor")
            if typed_extractor is not None and not isinstance(typed_extractor, dict):
                raise ValueError("typed extractor identity is invalid")
            extractor_revision = (
                typed_extractor.get("extractor_revision")
                if typed_extractor is not None
                else (
                    "deeplaw-deterministic/v2"
                    if compiler.get("typed_extraction") == "deterministic-v2"
                    else "deeplaw-deterministic/v1"
                )
            )
            prompt_configuration = {
                "mode": compiler.get("typed_extraction", "off"),
                "compiler_schema": compiler["schema_version"],
            }
            if typed_extractor is not None:
                prompt_configuration["prompt_config_sha256"] = typed_extractor.get(
                    "prompt_config_sha256"
                )
                prompt_configuration["manifest_sha256"] = typed_extractor.get("manifest_sha256")
                prompt_configuration["output_sha256"] = typed_extractor.get("output_sha256")
                prompt_configuration["network_policy"] = typed_extractor.get("network_policy")
                prompt_configuration["disclosure"] = typed_extractor.get("disclosure")
            identity = register_compilation_identity(
                self.connection,
                vault_id=self.vault_id,
                collection_id=identity_collection_id,
                collection_name=compiler.get("collection_name", "project"),
                logical_path=identity_logical_path,
                source_key=source_key,
                legacy_source_id=source_id,
                content_sha256=content_sha256,
                media_identity=media_type,
                origin_uri=origin_uri,
                byte_size=byte_size,
                observed_at=imported_at,
                adapter=(
                    compiler.get("source_adapter")
                    or compiler.get("extractor")
                    or compiler.get("adapter_schema")
                    or "deeplaw-legacy-compiler"
                ),
                adapter_version=(
                    compiler.get("source_adapter_version")
                    or compiler.get("extractor_version")
                    or compiler.get("schema_version")
                    or "unknown"
                ),
                configuration={
                    "format": compiler.get("format"),
                    "configuration": compiler.get("configuration", []),
                    "pdf_fallback": compiler.get("pdf_fallback"),
                    "source_ir_projection": "deeplaw.source-ir-projection/v1",
                },
                source_ir_nodes=prepared_source_ir_nodes,
                fragments=fragment_rows,
                extractor=(
                    typed_extractor.get("extractor")
                    if typed_extractor is not None
                    else compiler.get("typed_extraction", "off")
                ),
                extractor_revision=extractor_revision,
                model_identity=(
                    typed_extractor.get("model_identity") if typed_extractor is not None else None
                ),
                prompt_configuration=prompt_configuration,
                proposals=identity_proposals,
                source_trust=trust,
                source_sensitivity=sensitivity,
            )
            revision, audit_head = self._append_event(
                event_type="source_compiled",
                object_id=source_id,
                payload={
                    "source_sha256": content_sha256,
                    "fragment_count": identity["fragment_count"],
                    "fragment_inventory_sha256": identity["fragment_inventory_sha256"],
                    "proposal_count": identity["proposal_count"],
                    "proposal_inventory_sha256": identity["proposal_inventory_sha256"],
                    "proposal_ref_graph_sha256": identity["proposal_ref_graph_sha256"],
                    "asset_count": identity["proposal_count"],
                    "membership_sha256": sha256_bytes(
                        canonical_json(
                            {
                                "fragment_inventory_sha256": identity["fragment_inventory_sha256"],
                                "proposal_inventory_sha256": identity["proposal_inventory_sha256"],
                                "proposal_ref_graph_sha256": identity["proposal_ref_graph_sha256"],
                            }
                        ).encode("utf-8")
                    ),
                    "instruction_risk": instruction_risk,
                    "compiler": compiler,
                    "source_key": source_key,
                    "previous_source_id": previous_source_id,
                    "source_status": "pending",
                    "source_revision_id": identity["source_revision_id"],
                    "compilation_id": identity["compilation_id"],
                    "proposal_set_id": identity["proposal_set_id"],
                },
            )
            revision, audit_head = self._append_identity_snapshot(
                reason="source_compiled",
                source_revision_id=identity["source_revision_id"],
            )
            self.connection.commit()
        except BaseException:
            self.connection.rollback()
            raise
        return {
            "schema_version": "deeplaw.knowledge-ingest/v1",
            "vault_id": self.vault_id,
            "revision": revision,
            "audit_head": audit_head,
            "source": self.source_info(source_id),
            "asset_ids": asset_ids,
            "identity": identity,
            "idempotent": False,
        }

    def _insert_asset(
        self,
        *,
        kind: AssetKind,
        memory_tier: MemoryTier,
        title: str,
        statement: str,
        semantic_key: str | None,
        status: AssetStatus,
        verification: Literal["unverified", "source_bound"],
        trust: TrustLevel,
        sensitivity: Sensitivity,
        source_refs: tuple[SourceReference, ...],
        tags: tuple[str, ...],
        warnings: tuple[str, ...],
        expires_at: str | None,
        supersedes_asset_id: str | None,
        origin_uri: str | None,
        created_at: str,
    ) -> tuple[KnowledgeAsset, bool]:
        if kind not in ASSET_KINDS or memory_tier not in MEMORY_TIERS:
            raise ValueError("unsupported asset kind or memory tier")
        if status not in {"proposed", "quarantined"}:
            raise ValueError("new knowledge assets must start proposed or quarantined")
        title = title.strip()
        statement = statement.strip()
        semantic_key = semantic_key.strip() if semantic_key else None
        tags = tuple(sorted(dict.fromkeys(tag.strip() for tag in tags if tag.strip())))
        warnings = tuple(dict.fromkeys(warning.strip() for warning in warnings if warning.strip()))
        if expires_at is not None:
            expires_at = canonical_timestamp(expires_at, field="asset expires_at")
        content_sha256 = asset_content_sha256(
            kind=kind,
            memory_tier=memory_tier,
            title=title,
            statement=statement,
            semantic_key=semantic_key,
            trust=trust,
            sensitivity=sensitivity,
            source_refs=source_refs,
            tags=tags,
            warnings=warnings,
            expires_at=expires_at,
            supersedes_asset_id=supersedes_asset_id,
            origin_uri=origin_uri,
        )
        asset_id = stable_id("asset", self.vault_id, content_sha256)
        asset = KnowledgeAsset(
            asset_id=asset_id,
            vault_id=self.vault_id,
            kind=kind,
            memory_tier=memory_tier,
            title=title,
            statement=statement,
            semantic_key=semantic_key,
            status=status,
            verification=verification,
            trust=trust,
            sensitivity=sensitivity,
            source_refs=source_refs,
            tags=tags,
            warnings=warnings,
            created_at=created_at,
            activated_at=None,
            expires_at=expires_at,
            supersedes_asset_id=supersedes_asset_id,
            origin_uri=origin_uri,
            content_sha256=content_sha256,
        )
        existing = self.connection.execute(
            "SELECT asset_id FROM assets WHERE asset_id = ?",
            (asset_id,),
        ).fetchone()
        if existing is not None:
            return self.get_asset(asset_id, include_inactive=True), False
        if supersedes_asset_id is not None:
            superseded = self.connection.execute(
                "SELECT semantic_key FROM assets WHERE asset_id = ?",
                (supersedes_asset_id,),
            ).fetchone()
            if superseded is None:
                raise ValueError("supersedes_asset_id does not exist in this vault")
            if semantic_key is None or superseded["semantic_key"] != semantic_key:
                raise ValueError("superseding assets must retain the same semantic_key")
        self.connection.execute(
            "INSERT INTO assets VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                asset.asset_id,
                asset.kind,
                asset.memory_tier,
                asset.title,
                asset.statement,
                asset.semantic_key,
                asset.status,
                asset.verification,
                asset.trust,
                asset.sensitivity,
                canonical_json([reference.to_dict() for reference in asset.source_refs]),
                canonical_json(list(asset.tags)),
                canonical_json(list(asset.warnings)),
                asset.created_at,
                asset.activated_at,
                asset.expires_at,
                asset.supersedes_asset_id,
                asset.origin_uri,
                asset.content_sha256,
            ),
        )
        self.connection.execute(
            "INSERT INTO asset_search VALUES (?, ?, ?, ?, ?)",
            (
                asset.asset_id,
                _token_string(asset.title),
                _token_string(asset.statement),
                _token_string(asset.semantic_key or ""),
                _token_string(" ".join(asset.tags)),
            ),
        )
        return asset, True

    def _asset_revision_identity(
        self,
        asset_id: str,
    ) -> dict[str, Any] | None:
        """Return the exact Identity v2 binding for one legacy Asset."""
        if not self.identity_v2_enabled:
            return None
        row = self.connection.execute(
            """
            SELECT asset_revision_bindings_v2.asset_revision_id,
                   knowledge_revisions_v2.knowledge_key,
                   knowledge_revisions_v2.logical_node_keys_json,
                   knowledge_revisions_v2.source_revision_ids_json
            FROM asset_revision_bindings_v2
            JOIN knowledge_revisions_v2 USING(asset_revision_id)
            WHERE asset_revision_bindings_v2.legacy_asset_id = ?
            """,
            (asset_id,),
        ).fetchone()
        if row is None:
            return None
        references = self.connection.execute(
            """
            SELECT source_revision_id, fragment_revision_id, locator, quote_sha256
            FROM proposal_source_refs_v2
            WHERE asset_revision_id = ?
            ORDER BY ref_ordinal
            """,
            (row["asset_revision_id"],),
        ).fetchall()
        return {
            "asset_revision_id": row["asset_revision_id"],
            "knowledge_key": row["knowledge_key"],
            "logical_node_keys": tuple(
                strict_json_loads(row["logical_node_keys_json"])
            ),
            "source_revision_ids": tuple(
                strict_json_loads(row["source_revision_ids_json"])
            ),
            "source_refs": tuple(dict(reference) for reference in references),
        }

    def _register_asset_revision_in_transaction(
        self,
        proposal: KnowledgeAsset,
        *,
        knowledge_key: str,
        logical_node_keys: tuple[str, ...],
        predecessor_revision_ids: tuple[str, ...] = (),
        lineage_status: Literal["modified", "split", "merged"] | None = None,
        mapping_evidence: dict[str, Any] | None = None,
        policy_id: str,
        created_at: str,
    ) -> dict[str, Any]:
        """Bind a derived source-backed proposal to exact immutable evidence.

        The caller owns the surrounding transaction and audit event. This helper is
        deliberately unavailable for source-free proposals: an Identity v2 Asset
        revision must retain at least one exact Source Reference.
        """
        self._require_write()
        if not self.identity_v2_enabled:
            raise RuntimeError("Knowledge Identity v2 is not installed")
        if not proposal.source_refs:
            raise ValueError("source-bound Asset revision requires source references")
        if proposal.verification != "source_bound":
            raise ValueError("source-backed proposal must be marked source_bound")
        if not logical_node_keys or len(logical_node_keys) > 100:
            raise ValueError("Asset revision logical node inventory is invalid")
        normalized_node_keys = tuple(dict.fromkeys(logical_node_keys))
        if len(normalized_node_keys) != len(logical_node_keys) or any(
            not isinstance(value, str) or not 1 <= len(value) <= 2_000
            for value in normalized_node_keys
        ):
            raise ValueError("Asset revision logical node inventory is invalid")

        prepared_refs: list[dict[str, str]] = []
        fragment_revision_ids: list[str] = []
        source_revision_ids: set[str] = set()
        seen_refs: set[tuple[str, str]] = set()
        for reference in proposal.source_refs:
            row = self.connection.execute(
                """
                SELECT source_revision_bindings_v2.source_revision_id,
                       legacy_fragment_bindings_v2.fragment_revision_id,
                       source_fragments.locator,
                       source_fragments.text_sha256
                FROM source_revision_bindings_v2
                JOIN legacy_fragment_bindings_v2 USING(legacy_source_id)
                JOIN source_fragments USING(fragment_id)
                WHERE source_revision_bindings_v2.legacy_source_id = ?
                  AND legacy_fragment_bindings_v2.fragment_id = ?
                """,
                (reference.source_id, reference.fragment_id),
            ).fetchone()
            if row is None or (
                row["locator"], row["text_sha256"]
            ) != (reference.locator, reference.quote_sha256):
                raise RuntimeError(
                    "derived proposal source reference identity is unavailable or changed"
                )
            ref_key = (row["source_revision_id"], row["fragment_revision_id"])
            if ref_key in seen_refs:
                raise ValueError("derived proposal source references must be unique")
            seen_refs.add(ref_key)
            source_revision_ids.add(row["source_revision_id"])
            fragment_revision_ids.append(row["fragment_revision_id"])
            prepared_refs.append(
                {
                    "source_revision_id": row["source_revision_id"],
                    "fragment_revision_id": row["fragment_revision_id"],
                    "locator": reference.locator,
                    "quote_sha256": reference.quote_sha256,
                }
            )

        placeholders = ",".join("?" for _ in fragment_revision_ids)
        available_node_keys = {
            row["logical_node_key"]
            for row in self.connection.execute(
                f"""
                SELECT DISTINCT source_ir_nodes_v2.logical_node_key
                FROM fragment_node_membership_v2
                JOIN source_ir_nodes_v2 USING(node_id)
                WHERE fragment_node_membership_v2.fragment_revision_id
                      IN ({placeholders})
                """,
                tuple(fragment_revision_ids),
            ).fetchall()
        }
        if not set(normalized_node_keys).issubset(available_node_keys):
            raise ValueError(
                "derived proposal logical nodes are not backed by its exact fragments"
            )

        ordered_source_revision_ids = tuple(sorted(source_revision_ids))
        knowledge_content_sha256 = sha256_bytes(
            canonical_json(
                {
                    "kind": proposal.kind,
                    "memory_tier": proposal.memory_tier,
                    "title": proposal.title,
                    "statement": proposal.statement,
                    "knowledge_key": knowledge_key,
                    "logical_node_keys": list(normalized_node_keys),
                    "source_refs": [
                        {
                            "locator": reference.locator,
                            "quote_sha256": reference.quote_sha256,
                        }
                        for reference in proposal.source_refs
                    ],
                }
            ).encode("utf-8")
        )
        asset_revision_id = make_asset_revision_id(
            knowledge_key=knowledge_key,
            knowledge_content_sha256=knowledge_content_sha256,
            source_revision_ids=ordered_source_revision_ids,
        )
        self.connection.execute(
            "INSERT INTO knowledge_revisions_v2 VALUES (?, ?, ?, ?, ?)",
            (
                asset_revision_id,
                knowledge_key,
                canonical_json(list(normalized_node_keys)),
                knowledge_content_sha256,
                canonical_json(list(ordered_source_revision_ids)),
            ),
        )
        self.connection.execute(
            """
            INSERT INTO asset_revision_bindings_v2
            VALUES (?, ?, ?, NULL, NULL, ?)
            """,
            (
                proposal.asset_id,
                proposal.source_refs[0].source_id,
                asset_revision_id,
                created_at,
            ),
        )
        for ordinal, reference in enumerate(prepared_refs, start=1):
            self.connection.execute(
                "INSERT INTO proposal_source_refs_v2 VALUES (?, ?, ?, ?, ?, ?)",
                (
                    asset_revision_id,
                    ordinal,
                    reference["source_revision_id"],
                    reference["fragment_revision_id"],
                    reference["locator"],
                    reference["quote_sha256"],
                ),
            )
        lineage_id = None
        if lineage_status is not None:
            if mapping_evidence is None:
                raise ValueError("Asset revision lineage requires mapping evidence")
            lineage_id = record_lineage_transition(
                self.connection,
                knowledge_key=knowledge_key,
                from_asset_revision_ids=predecessor_revision_ids,
                to_asset_revision_ids=(asset_revision_id,),
                status=lineage_status,
                source_revision_id=ordered_source_revision_ids[0],
                mapping_evidence=mapping_evidence,
                created_at=created_at,
            )
        governance_revision = record_governance_revision(
            self.connection,
            subject_kind="asset_revision",
            subject_id=asset_revision_id,
            trust=proposal.trust,
            sensitivity=proposal.sensitivity,
            policy_id=policy_id,
            review_status="unreviewed",
            lifecycle_status="quarantined",
            reviewer_id=None,
            recorded_at=created_at,
        )
        return {
            "asset_revision_id": asset_revision_id,
            "knowledge_key": knowledge_key,
            "source_revision_ids": ordered_source_revision_ids,
            "logical_node_keys": normalized_node_keys,
            "source_refs": tuple(prepared_refs),
            "lineage_id": lineage_id,
            "governance_revision": governance_revision,
        }

    def propose_asset(
        self,
        *,
        kind: AssetKind,
        memory_tier: MemoryTier,
        title: str,
        statement: str,
        semantic_key: str | None = None,
        trust: TrustLevel = "user_provided",
        sensitivity: Sensitivity = "private",
        tags: Iterable[str] = (),
        expires_at: str | None = None,
        supersedes_asset_id: str | None = None,
        origin_uri: str | None = None,
        quarantined: bool = False,
    ) -> KnowledgeAsset:
        self._require_write()
        if trust == "verified_source":
            raise ValueError(
                "verified_source is reserved for a future publisher-verification "
                "workflow; use user_provided or untrusted"
            )
        instruction_risk = has_instruction_risk(f"{title}\n{statement}")
        quarantined = quarantined or instruction_risk
        warnings = (
            (
                "instruction-like or invisible control content detected; "
                "proposal requires explicit review"
            )
            if instruction_risk
            else "proposal was quarantined before review"
        )
        try:
            self.connection.execute("BEGIN IMMEDIATE")
            self._require_healthy_integrity()
            asset, inserted = self._insert_asset(
                kind=kind,
                memory_tier=memory_tier,
                title=title,
                statement=statement,
                semantic_key=semantic_key,
                status="quarantined" if quarantined else "proposed",
                verification="unverified",
                trust=trust,
                sensitivity=sensitivity,
                source_refs=(),
                tags=tuple(tags),
                warnings=(warnings,) if quarantined else (),
                expires_at=expires_at,
                supersedes_asset_id=supersedes_asset_id,
                origin_uri=origin_uri,
                created_at=utc_now(),
            )
            if not inserted:
                self.connection.rollback()
                return asset
            self._append_event(
                event_type="asset_proposed",
                object_id=asset.asset_id,
                payload={
                    "content_sha256": asset.content_sha256,
                    "status": asset.status,
                },
            )
            self.connection.commit()
            return asset
        except BaseException:
            self.connection.rollback()
            raise

    def propose_asset_revision(
        self,
        predecessor_asset_id: str,
        *,
        title: str,
        statement: str,
        origin_uri: str,
    ) -> KnowledgeAsset:
        """Create a quarantined, source-bound edit without inheriting approval."""
        self._require_write()
        predecessor = self.get_asset(predecessor_asset_id)
        warnings = ["projection edit requires exact source revalidation before approval"]
        if has_instruction_risk(f"{title}\n{statement}"):
            warnings.append(
                "instruction-like or invisible control content detected; "
                "proposal requires explicit review"
            )
        created_at = utc_now()
        try:
            self.connection.execute("BEGIN IMMEDIATE")
            self._require_healthy_integrity()
            proposal, inserted = self._insert_asset(
                kind=predecessor.kind,
                memory_tier=predecessor.memory_tier,
                title=title,
                statement=statement,
                semantic_key=predecessor.semantic_key,
                status="quarantined",
                verification=("source_bound" if predecessor.source_refs else "unverified"),
                trust=predecessor.trust,
                sensitivity=predecessor.sensitivity,
                source_refs=predecessor.source_refs,
                tags=predecessor.tags,
                warnings=tuple(warnings),
                expires_at=predecessor.expires_at,
                supersedes_asset_id=predecessor.asset_id,
                origin_uri=origin_uri,
                created_at=created_at,
            )
            if not inserted:
                self.connection.rollback()
                return proposal
            identity: dict[str, Any] | None = None
            if self.identity_v2_enabled and predecessor.source_refs:
                prior = self._asset_revision_identity(predecessor.asset_id)
                if prior is None:
                    raise RuntimeError("projection predecessor Identity v2 binding is unavailable")
                identity = self._register_asset_revision_in_transaction(
                    proposal,
                    knowledge_key=prior["knowledge_key"],
                    logical_node_keys=prior["logical_node_keys"],
                    predecessor_revision_ids=(prior["asset_revision_id"],),
                    lineage_status="modified",
                    mapping_evidence={
                        "method": "human-projection-edit",
                        "predecessor_asset_id": predecessor.asset_id,
                        "origin_uri": origin_uri,
                        "approval_inherited": False,
                    },
                    policy_id="deeplaw.projection-edit/v1",
                    created_at=created_at,
                )
            self._append_event(
                event_type="asset_revision_proposed",
                object_id=proposal.asset_id,
                payload={
                    "content_sha256": proposal.content_sha256,
                    "status": proposal.status,
                    "verification": proposal.verification,
                    "predecessor_asset_ids": [predecessor.asset_id],
                    "source_ref_count": len(proposal.source_refs),
                    "lineage_status": "modified",
                    "transformation": "edit",
                    "approval_inherited": False,
                },
            )
            if identity is not None:
                self._append_identity_snapshot(
                    reason="asset_revision_proposed",
                    source_revision_id=identity["source_revision_ids"][0],
                )
            self.connection.commit()
            return proposal
        except BaseException:
            self.connection.rollback()
            raise

    def source_review_manifest(self, source_id: str) -> dict[str, Any]:
        self._require_control()
        source = self.source_info(source_id)
        proposal_set_id = source.get("proposal_set_id")
        if proposal_set_id is not None:
            rows = self.connection.execute(
                """
                SELECT assets.asset_id, assets.status, assets.content_sha256
                FROM asset_revision_bindings_v2
                JOIN assets
                  ON assets.asset_id = asset_revision_bindings_v2.legacy_asset_id
                WHERE asset_revision_bindings_v2.legacy_source_id = ?
                  AND asset_revision_bindings_v2.proposal_set_id = ?
                ORDER BY asset_revision_bindings_v2.proposal_ordinal
                """,
                (source_id, proposal_set_id),
            ).fetchall()
            proposal_set = self.connection.execute(
                "SELECT * FROM proposal_sets_v2 WHERE proposal_set_id = ?",
                (proposal_set_id,),
            ).fetchone()
            compilation = self.connection.execute(
                "SELECT * FROM compilations_v2 WHERE compilation_id = ?",
                (source["compilation_id"],),
            ).fetchone()
            if proposal_set is None or compilation is None:
                raise RuntimeError("source Identity v2 review inventory is incomplete")
            fragment_count = self.connection.execute(
                "SELECT COUNT(*) FROM source_fragments WHERE source_id = ?",
                (source_id,),
            ).fetchone()[0]
            fragment_inventory_sha256 = compilation["fragment_inventory_sha256"]
            proposal_inventory_sha256 = proposal_set["proposal_inventory_sha256"]
            proposal_ref_graph_sha256 = proposal_set["proposal_ref_graph_sha256"]
        else:
            rows = self.connection.execute(
                """
                SELECT DISTINCT assets.asset_id, assets.status, assets.content_sha256
                FROM assets, json_each(assets.source_refs_json) AS reference
                WHERE json_extract(reference.value, '$.source_id') = ?
                  AND json_array_length(assets.source_refs_json) = 1
                ORDER BY assets.asset_id
                """,
                (source_id,),
            ).fetchall()
            fragment_count = self.connection.execute(
                "SELECT COUNT(*) FROM source_fragments WHERE source_id = ?",
                (source_id,),
            ).fetchone()[0]
            fragment_inventory_sha256 = sha256_bytes(
                canonical_json(
                    [
                        dict(row)
                        for row in self.connection.execute(
                            """
                            SELECT fragment_id, ordinal, locator, text_sha256
                            FROM source_fragments WHERE source_id = ? ORDER BY ordinal
                            """,
                            (source_id,),
                        )
                    ]
                ).encode("utf-8")
            )
            proposal_inventory_sha256 = sha256_bytes(
                canonical_json(
                    [
                        {
                            "asset_id": row["asset_id"],
                            "content_sha256": row["content_sha256"],
                        }
                        for row in rows
                    ]
                ).encode("utf-8")
            )
            proposal_ref_graph_sha256 = sha256_bytes(
                canonical_json(
                    [
                        {
                            "asset_id": row["asset_id"],
                            "source_refs": strict_json_loads(
                                self.connection.execute(
                                    "SELECT source_refs_json FROM assets WHERE asset_id = ?",
                                    (row["asset_id"],),
                                ).fetchone()["source_refs_json"]
                            ),
                        }
                        for row in rows
                    ]
                ).encode("utf-8")
            )
        membership = [
            {
                "asset_id": row["asset_id"],
                "content_sha256": row["content_sha256"],
                "status": row["status"],
            }
            for row in rows
        ]
        membership_sha256 = sha256_bytes(canonical_json(membership).encode("utf-8"))
        visible_asset_ids = [item["asset_id"] for item in membership[:100]]
        body = {
            "schema_version": "deeplaw.knowledge-review-manifest/v2",
            "vault_id": self.vault_id,
            "source_key": source["source_key"],
            "source_id": source_id,
            "source_revision_id": source.get("source_revision_id"),
            "compilation_id": source.get("compilation_id"),
            "proposal_set_id": proposal_set_id,
            "source_content_sha256": source["content_sha256"],
            "source_status": source["status"],
            "fragment_count": fragment_count,
            "fragment_inventory_sha256": fragment_inventory_sha256,
            "proposal_count": len(membership),
            "pending_proposal_count": sum(
                item["status"] in {"proposed", "quarantined"} for item in membership
            ),
            "proposal_inventory_sha256": proposal_inventory_sha256,
            "proposal_ref_graph_sha256": proposal_ref_graph_sha256,
            "quarantine_count": sum(item["status"] == "quarantined" for item in membership),
            "review_member_count": len(membership),
            "review_membership_sha256": membership_sha256,
            "asset_ids": visible_asset_ids,
            "asset_ids_truncated": len(visible_asset_ids) < len(membership),
        }
        return {
            **body,
            "review_manifest_sha256": sha256_bytes(canonical_json(body).encode("utf-8")),
        }

    def _record_review_receipt(
        self,
        *,
        assets: list[KnowledgeAsset],
        source_id: str | None,
        reviewer_id: str,
        policy_id: str,
        reason: str,
        review_manifest_sha256: str,
        decision: Literal["approve", "reject"] = "approve",
    ) -> dict[str, Any]:
        reviewer_id = reviewer_id.strip()
        policy_id = policy_id.strip()
        reason = reason.strip()
        if not 1 <= len(reviewer_id) <= 200:
            raise ValueError("reviewer_id must be between 1 and 200 characters")
        if not 1 <= len(policy_id) <= 200:
            raise ValueError("review policy_id must be between 1 and 200 characters")
        if not 1 <= len(reason) <= 2_000:
            raise ValueError("review reason must be between 1 and 2000 characters")
        if not _SHA256.fullmatch(review_manifest_sha256):
            raise ValueError("review manifest hash is invalid")
        reviewed_at = utc_now()
        proposal_ids = [asset.asset_id for asset in assets]
        asset_hashes = [asset.content_sha256 for asset in assets]
        decisions = [{"asset_id": asset.asset_id, "decision": decision} for asset in assets]
        body = {
            "schema_version": "deeplaw.knowledge-review-receipt/v1",
            "vault_id": self.vault_id,
            "reviewer_id": reviewer_id,
            "reviewed_at": reviewed_at,
            "policy_id": policy_id,
            "source_id": source_id,
            "proposal_ids": proposal_ids,
            "asset_hashes": asset_hashes,
            "decisions": decisions,
            "reason": reason,
            "review_manifest_sha256": review_manifest_sha256,
            "signature": None,
        }
        receipt_sha256 = sha256_bytes(canonical_json(body).encode("utf-8"))
        receipt_id = stable_id(
            "review",
            self.vault_id,
            receipt_sha256,
            str(self.revision + 1),
        )
        self.connection.execute(
            """
            INSERT INTO review_receipts VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                receipt_id,
                reviewer_id,
                reviewed_at,
                policy_id,
                source_id,
                canonical_json(proposal_ids),
                canonical_json(asset_hashes),
                canonical_json(decisions),
                reason,
                review_manifest_sha256,
                None,
                receipt_sha256,
            ),
        )
        self._append_event(
            event_type="review_recorded",
            object_id=receipt_id,
            payload={
                "receipt_sha256": receipt_sha256,
                "review_manifest_sha256": review_manifest_sha256,
                "source_id": source_id,
                "asset_count": len(assets),
            },
        )
        return {
            **body,
            "review_receipt_id": receipt_id,
            "receipt_sha256": receipt_sha256,
        }

    def get_review_receipt(self, review_receipt_id: str) -> dict[str, Any]:
        self._require_control()
        if not isinstance(review_receipt_id, str) or not _REVIEW_RECEIPT_ID.fullmatch(
            review_receipt_id
        ):
            raise ValueError("review receipt ID is invalid")
        row = self.connection.execute(
            "SELECT * FROM review_receipts WHERE review_receipt_id = ?",
            (review_receipt_id,),
        ).fetchone()
        if row is None:
            raise KeyError(f"review receipt is unavailable: {review_receipt_id}")
        body = {
            "schema_version": "deeplaw.knowledge-review-receipt/v1",
            "vault_id": self.vault_id,
            "reviewer_id": row["reviewer_id"],
            "reviewed_at": row["reviewed_at"],
            "policy_id": row["policy_id"],
            "source_id": row["source_id"],
            "proposal_ids": strict_json_loads(row["proposal_ids_json"]),
            "asset_hashes": strict_json_loads(row["asset_hashes_json"]),
            "decisions": strict_json_loads(row["decisions_json"]),
            "reason": row["reason"],
            "review_manifest_sha256": row["review_manifest_sha256"],
            "signature": (
                strict_json_loads(row["signature_json"])
                if row["signature_json"] is not None
                else None
            ),
        }
        record_valid = sha256_bytes(canonical_json(body).encode("utf-8")) == row["receipt_sha256"]
        vault_integrity_valid = self.verify_integrity()["valid"]
        return {
            **body,
            "review_receipt_id": review_receipt_id,
            "receipt_sha256": row["receipt_sha256"],
            "record_valid": record_valid,
            "vault_integrity_valid": vault_integrity_valid,
            "valid": bool(record_valid and vault_integrity_valid),
        }

    def review_queue(
        self,
        *,
        source_id: str | None = None,
        kind: str | None = None,
        status: str | None = None,
        limit: int = 100,
    ) -> dict[str, Any]:
        self._require_control()
        if isinstance(limit, bool) or not 1 <= limit <= 500:
            raise ValueError("review queue limit must be between 1 and 500")
        if kind is not None and kind not in ASSET_KINDS:
            raise ValueError("review queue kind is invalid")
        if status is not None and status not in {"proposed", "quarantined"}:
            raise ValueError("review queue status is invalid")
        if source_id is not None and not _SOURCE_ID.fullmatch(source_id):
            raise ValueError("review queue source ID is invalid")
        clauses = ["assets.status IN ('proposed', 'quarantined')"]
        parameters: list[Any] = []
        if kind is not None:
            clauses.append("assets.kind = ?")
            parameters.append(kind)
        if status is not None:
            clauses.append("assets.status = ?")
            parameters.append(status)
        if source_id is not None:
            clauses.append(
                "EXISTS (SELECT 1 FROM json_each(assets.source_refs_json) AS ref "
                "WHERE json_extract(ref.value, '$.source_id') = ?)"
            )
            parameters.append(source_id)
        where = " AND ".join(clauses)
        total = self.connection.execute(
            f"SELECT COUNT(*) FROM assets WHERE {where}",
            parameters,
        ).fetchone()[0]
        rows = self.connection.execute(
            f"SELECT * FROM assets WHERE {where} ORDER BY created_at, asset_id LIMIT ?",
            (*parameters, limit),
        ).fetchall()
        items = []
        for row in rows:
            asset = self._row_to_asset(row)
            items.append(
                {
                    "asset_id": asset.asset_id,
                    "kind": asset.kind,
                    "title": asset.title,
                    "status": asset.status,
                    "sensitivity": asset.sensitivity,
                    "source_ids": sorted({reference.source_id for reference in asset.source_refs}),
                    "content_sha256": asset.content_sha256,
                    "instruction_risk": bool(asset.warnings),
                    "created_at": asset.created_at,
                }
            )
        return {
            "schema_version": "deeplaw.knowledge-review-queue/v1",
            "vault_id": self.vault_id,
            "total": total,
            "returned": len(items),
            "truncated": len(items) < total,
            "items": items,
        }

    def latest_review_receipt_for_asset(self, asset_id: str) -> dict[str, Any] | None:
        self._require_control()
        if not isinstance(asset_id, str) or not _ASSET_ID.fullmatch(asset_id):
            raise ValueError("knowledge asset ID is invalid")
        row = self.connection.execute(
            """
            SELECT review_receipts.review_receipt_id
            FROM review_receipts,
                 json_each(review_receipts.decisions_json) AS decision
            WHERE json_extract(decision.value, '$.asset_id') = ?
            ORDER BY review_receipts.reviewed_at DESC,
                     review_receipts.review_receipt_id DESC
            LIMIT 1
            """,
            (asset_id,),
        ).fetchone()
        return self.get_review_receipt(row["review_receipt_id"]) if row is not None else None

    def _reject_asset_in_transaction(
        self,
        asset_id: str,
        *,
        reason: str,
        reviewer_id: str,
        policy_id: str = "deeplaw.local-review/v1",
    ) -> dict[str, Any]:
        """Reject one pending proposal inside the caller's open transaction."""
        proposal = self.get_asset(asset_id, include_inactive=True)
        if proposal.status not in {"proposed", "quarantined"}:
            raise ValueError("only proposed or quarantined assets can be rejected")
        manifest_body = {
            "schema_version": "deeplaw.knowledge-review-manifest/v1",
            "vault_id": self.vault_id,
            "source_id": None,
            "asset_id": proposal.asset_id,
            "content_sha256": proposal.content_sha256,
            "status": proposal.status,
        }
        manifest_sha256 = sha256_bytes(canonical_json(manifest_body).encode("utf-8"))
        self.connection.execute(
            "UPDATE assets SET status = 'revoked' WHERE asset_id = ?",
            (asset_id,),
        )
        self._append_event(
            event_type="asset_revoked",
            object_id=asset_id,
            payload={"reason": reason, "content_sha256": proposal.content_sha256},
        )
        identity = self._asset_revision_identity(asset_id)
        if identity is not None:
            recorded_at = utc_now()
            latest = self.connection.execute(
                """
                SELECT recorded_at
                FROM governance_revisions_v2
                WHERE subject_kind = 'asset_revision' AND subject_id = ?
                ORDER BY recorded_at DESC, governance_revision DESC
                LIMIT 1
                """,
                (identity["asset_revision_id"],),
            ).fetchone()
            if latest is not None:
                recorded_at = _timestamp_after(recorded_at, latest["recorded_at"])
            record_governance_revision(
                self.connection,
                subject_kind="asset_revision",
                subject_id=identity["asset_revision_id"],
                trust=proposal.trust,
                sensitivity=proposal.sensitivity,
                policy_id=policy_id,
                review_status="human_verified",
                lifecycle_status="revoked",
                activation_status="inactive",
                revoked_at=recorded_at,
                export_allowed=False,
                reviewer_id=reviewer_id,
                recorded_at=recorded_at,
            )
        receipt = self._record_review_receipt(
            assets=[proposal],
            source_id=None,
            reviewer_id=reviewer_id,
            policy_id=policy_id,
            reason=reason,
            review_manifest_sha256=manifest_sha256,
            decision="reject",
        )
        return {
            "proposal": proposal,
            "review_receipt": receipt,
            "identity": identity,
        }

    def reject_asset(
        self,
        asset_id: str,
        *,
        reason: str,
        reviewer_id: str,
        confirm_reviewed: bool,
    ) -> dict[str, Any]:
        self._require_write()
        self._require_control()
        if not confirm_reviewed:
            raise ValueError("asset rejection requires explicit reviewed confirmation")
        reason = reason.strip()
        if not 1 <= len(reason) <= 2_000:
            raise ValueError("review rejection reason must be between 1 and 2000 characters")
        try:
            self.connection.execute("BEGIN IMMEDIATE")
            self._require_healthy_integrity()
            rejected = self._reject_asset_in_transaction(
                asset_id,
                reason=reason,
                reviewer_id=reviewer_id,
            )
            identity = rejected["identity"]
            if identity is not None:
                self._append_identity_snapshot(
                    reason="governance_recorded",
                    source_revision_id=identity["source_revision_ids"][0],
                )
            self.connection.commit()
        except BaseException:
            self.connection.rollback()
            raise
        return {
            "schema_version": "deeplaw.knowledge-review-decision/v1",
            "asset_id": asset_id,
            "decision": "reject",
            "status": "revoked",
            "review_receipt": rejected["review_receipt"],
            "revision": self.revision,
            "audit_head": self.audit_head,
        }

    def record_run_receipt(
        self,
        payload: dict[str, Any],
        *,
        capsule: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        self._require_write()
        self._require_control()
        if not isinstance(capsule, dict):
            raise ValueError("run receipt requires a verified knowledge Capsule")
        from .context_compiler import verify_capsule

        try:
            capsule_verification = verify_capsule(capsule, vault=self)
        except (KeyError, TypeError, ValueError) as error:
            raise ValueError("run receipt requires a verified knowledge Capsule") from error
        if not capsule_verification["valid"]:
            raise ValueError("run receipt requires a verified knowledge Capsule")
        expected = {
            "schema_version",
            "vault_id",
            "vault_revision",
            "audit_head",
            "capsule_id",
            "capsule_digest",
            "task_sha256",
            "goal_sha256",
            "selected_asset_ids",
            "source_ids",
            "host",
            "model",
            "started_at",
            "finished_at",
            "status",
            "outcome_artifact_sha256",
            "metrics",
        }
        if not isinstance(payload, dict) or set(payload) != expected:
            raise ValueError("run receipt payload does not match its closed contract")
        if (
            payload["schema_version"] != "deeplaw.knowledge-run-receipt/v1"
            or payload["vault_id"] != self.vault_id
            or isinstance(payload["vault_revision"], bool)
            or not isinstance(payload["vault_revision"], int)
            or not 0 <= payload["vault_revision"] <= self.revision
            or not _SHA256.fullmatch(payload["audit_head"])
            or self.audit_hash_at(payload["vault_revision"]) != payload["audit_head"]
            or not isinstance(payload["capsule_id"], str)
            or not payload["capsule_id"].startswith("capsule_")
            or not _SHA256.fullmatch(payload["capsule_digest"])
            or not _SHA256.fullmatch(payload["task_sha256"])
            or (
                payload["goal_sha256"] is not None and not _SHA256.fullmatch(payload["goal_sha256"])
            )
            or payload["status"] not in {"success", "partial", "failure", "refusal", "timeout"}
            or (
                payload["outcome_artifact_sha256"] is not None
                and not _SHA256.fullmatch(payload["outcome_artifact_sha256"])
            )
        ):
            raise ValueError("run receipt identity or status is invalid")
        selected_asset_ids = payload["selected_asset_ids"]
        source_ids = payload["source_ids"]
        if (
            not isinstance(selected_asset_ids, list)
            or len(selected_asset_ids) > 100
            or len(selected_asset_ids) != len(set(selected_asset_ids))
            or any(
                not isinstance(asset_id, str) or not _ASSET_ID.fullmatch(asset_id)
                for asset_id in selected_asset_ids
            )
            or not isinstance(source_ids, list)
            or len(source_ids) > 100
            or len(source_ids) != len(set(source_ids))
            or any(
                not isinstance(source_id, str) or not _SOURCE_ID.fullmatch(source_id)
                for source_id in source_ids
            )
        ):
            raise ValueError("run receipt asset/source inventory is invalid")
        selected_capsule_items = [
            item
            for group_name in (
                "constraints",
                "decisions",
                "knowledge_assets",
                "experiences",
                "open_questions",
            )
            for item in capsule[group_name]
        ]
        expected_asset_ids = [item["asset_id"] for item in selected_capsule_items]
        expected_source_ids = sorted(
            {
                reference["source_id"]
                for item in selected_capsule_items
                for reference in item["source_refs"]
            }
        )
        expected_capsule_binding = {
            "vault_id": capsule["vault_id"],
            "vault_revision": capsule["vault_revision"],
            "audit_head": capsule["audit_head"],
            "capsule_id": capsule["capsule_id"],
            "capsule_digest": capsule["capsule_digest"],
            "task_sha256": sha256_bytes(capsule["task"].encode("utf-8")),
            "goal_sha256": (
                sha256_bytes(capsule["goal"].encode("utf-8"))
                if capsule["goal"] is not None
                else None
            ),
            "selected_asset_ids": expected_asset_ids,
            "source_ids": expected_source_ids,
        }
        if any(payload[field] != value for field, value in expected_capsule_binding.items()):
            raise ValueError("run receipt does not match its verified knowledge Capsule")
        for asset_id in selected_asset_ids:
            self.get_asset(asset_id, include_inactive=True)
        for source_id in source_ids:
            self.source_info(source_id)
        host = payload["host"]
        model = payload["model"]
        metrics = payload["metrics"]
        if (
            not isinstance(host, dict)
            or set(host) != {"name", "version"}
            or any(
                not isinstance(host[field], str) or not 1 <= len(host[field].strip()) <= 200
                for field in host
            )
            or (
                model is not None
                and (
                    not isinstance(model, dict)
                    or set(model) != {"name", "version"}
                    or any(
                        not isinstance(model[field], str)
                        or not 1 <= len(model[field].strip()) <= 200
                        for field in model
                    )
                )
            )
            or not isinstance(metrics, dict)
            or set(metrics) != {"input_tokens", "output_tokens", "latency_ms", "cost", "currency"}
        ):
            raise ValueError("run receipt host, model, or metrics are invalid")
        for field in ("input_tokens", "output_tokens"):
            value = metrics[field]
            if value is not None and (
                isinstance(value, bool) or not isinstance(value, int) or value < 0
            ):
                raise ValueError("run receipt token metrics are invalid")
        for field in ("latency_ms", "cost"):
            value = metrics[field]
            if value is not None and (
                isinstance(value, bool) or not isinstance(value, (int, float)) or value < 0
            ):
                raise ValueError("run receipt numeric metrics are invalid")
        if metrics["currency"] is not None and (
            not isinstance(metrics["currency"], str) or not 1 <= len(metrics["currency"]) <= 16
        ):
            raise ValueError("run receipt currency is invalid")
        started_at = canonical_timestamp(payload["started_at"], field="run started_at")
        finished_at = canonical_timestamp(payload["finished_at"], field="run finished_at")
        if finished_at < started_at:
            raise ValueError("run receipt finished_at precedes started_at")
        body = deepcopy(payload)
        body["started_at"] = started_at
        body["finished_at"] = finished_at
        receipt_sha256 = sha256_bytes(canonical_json(body).encode("utf-8"))
        run_id = stable_id("run", self.vault_id, receipt_sha256)
        existing = self.connection.execute(
            "SELECT receipt_sha256 FROM run_receipts WHERE run_id = ?",
            (run_id,),
        ).fetchone()
        if existing is not None:
            if existing["receipt_sha256"] != receipt_sha256:
                raise RuntimeError("run receipt identity collision")
            return self.get_run_receipt(run_id)
        try:
            self.connection.execute("BEGIN IMMEDIATE")
            self._require_healthy_integrity()
            self.connection.execute(
                "INSERT INTO run_receipts VALUES (?, ?, ?, ?)",
                (run_id, canonical_json(body), receipt_sha256, finished_at),
            )
            revision, audit_head = self._append_event(
                event_type="run_receipt_recorded",
                object_id=run_id,
                payload={"receipt_sha256": receipt_sha256},
            )
            self.connection.commit()
        except BaseException:
            self.connection.rollback()
            raise
        return {
            **body,
            "run_id": run_id,
            "receipt_sha256": receipt_sha256,
            "revision": revision,
            "current_audit_head": audit_head,
            "valid": True,
        }

    def get_run_receipt(self, run_id: str) -> dict[str, Any]:
        self._require_control()
        if not isinstance(run_id, str) or not _RUN_RECEIPT_ID.fullmatch(run_id):
            raise ValueError("run receipt ID is invalid")
        row = self.connection.execute(
            "SELECT * FROM run_receipts WHERE run_id = ?",
            (run_id,),
        ).fetchone()
        if row is None:
            raise KeyError(f"run receipt is unavailable: {run_id}")
        payload = strict_json_loads(row["payload_json"])
        record_valid = (
            isinstance(payload, dict)
            and sha256_bytes(canonical_json(payload).encode("utf-8")) == row["receipt_sha256"]
            and stable_id("run", self.vault_id, row["receipt_sha256"]) == run_id
        )
        vault_integrity_valid = self.verify_integrity()["valid"]
        return {
            **payload,
            "run_id": run_id,
            "receipt_sha256": row["receipt_sha256"],
            "record_valid": record_valid,
            "vault_integrity_valid": vault_integrity_valid,
            "valid": bool(record_valid and vault_integrity_valid),
        }

    def list_run_receipts(self, *, limit: int = 100) -> dict[str, Any]:
        self._require_control()
        if isinstance(limit, bool) or not 1 <= limit <= 500:
            raise ValueError("run receipt list limit must be between 1 and 500")
        total = self.connection.execute("SELECT COUNT(*) FROM run_receipts").fetchone()[0]
        rows = self.connection.execute(
            "SELECT run_id FROM run_receipts ORDER BY created_at DESC, run_id LIMIT ?",
            (limit,),
        ).fetchall()
        return {
            "schema_version": "deeplaw.knowledge-run-receipt-list/v1",
            "vault_id": self.vault_id,
            "total": total,
            "runs": [self.get_run_receipt(row["run_id"]) for row in rows],
            "truncated": len(rows) < total,
        }

    def record_feedback(self, payload: dict[str, Any]) -> dict[str, Any]:
        self._require_write()
        self._require_control()
        expected = {
            "schema_version",
            "vault_id",
            "run_id",
            "capsule_id",
            "capsule_digest",
            "vault_revision",
            "outcome",
            "helpful_asset_ids",
            "irrelevant_asset_ids",
            "harmful_asset_ids",
            "stale_asset_ids",
            "missing_knowledge",
            "missing_sources",
            "incorrect_relations",
            "budget_failures",
            "observation",
            "recommended_action",
            "review_status",
            "created_at",
            "regression_case",
            "sensitivity",
        }
        if not isinstance(payload, dict) or set(payload) != expected:
            raise ValueError("feedback payload does not match its closed contract")
        if (
            payload["schema_version"] != "deeplaw.knowledge-feedback-ledger/v1"
            or payload["vault_id"] != self.vault_id
            or payload["outcome"] not in {"success", "partial", "failure"}
            or payload["review_status"] != "proposed"
            or payload["sensitivity"] not in SENSITIVITY_LEVELS
            or not _RUN_RECEIPT_ID.fullmatch(payload["run_id"])
            or not _SHA256.fullmatch(payload["capsule_digest"])
            or isinstance(payload["vault_revision"], bool)
            or not isinstance(payload["vault_revision"], int)
        ):
            raise ValueError("feedback identity, outcome, or sensitivity is invalid")
        run = self.get_run_receipt(payload["run_id"])
        if (
            not run["valid"]
            or run["capsule_id"] != payload["capsule_id"]
            or run["capsule_digest"] != payload["capsule_digest"]
            or run["vault_revision"] != payload["vault_revision"]
        ):
            raise ValueError("feedback does not match its verified run receipt")
        selected_asset_ids = set(run["selected_asset_ids"])
        asset_fields = (
            "helpful_asset_ids",
            "irrelevant_asset_ids",
            "harmful_asset_ids",
            "stale_asset_ids",
        )
        inventories: list[list[str]] = []
        for field in asset_fields:
            values = payload[field]
            if (
                not isinstance(values, list)
                or len(values) > 100
                or len(values) != len(set(values))
                or any(
                    not isinstance(asset_id, str) or not _ASSET_ID.fullmatch(asset_id)
                    for asset_id in values
                )
            ):
                raise ValueError(f"feedback {field} is invalid")
            for asset_id in values:
                self.get_asset(asset_id, include_inactive=True)
                if asset_id not in selected_asset_ids:
                    raise ValueError(
                        "feedback asset classifications must refer to the bound run Capsule"
                    )
            inventories.append(values)
        flattened = [asset_id for values in inventories for asset_id in values]
        if len(flattened) != len(set(flattened)):
            raise ValueError("feedback asset classifications must be mutually exclusive")
        text_list_fields = (
            "missing_knowledge",
            "missing_sources",
            "incorrect_relations",
            "budget_failures",
        )
        for field in text_list_fields:
            values = payload[field]
            if (
                not isinstance(values, list)
                or len(values) > 32
                or any(
                    not isinstance(value, str) or not 1 <= len(value.strip()) <= 1_000
                    for value in values
                )
            ):
                raise ValueError(f"feedback {field} is invalid")
        for field in ("observation", "recommended_action"):
            if not isinstance(payload[field], str) or not 1 <= len(payload[field].strip()) <= 5_000:
                raise ValueError(f"feedback {field} is invalid")
        created_at = canonical_timestamp(payload["created_at"], field="feedback created_at")
        regression = payload["regression_case"]
        expected_regression_fields = {
            "case_id",
            "run_id",
            "capsule_id",
            "capsule_digest",
            "vault_revision",
            "task_sha256",
            "selected_asset_ids",
            "source_ids",
            "expected_helpful_asset_ids",
        }
        if (
            not isinstance(regression, dict)
            or set(regression) != expected_regression_fields
            or regression["run_id"] != payload["run_id"]
            or regression["capsule_id"] != run["capsule_id"]
            or regression["capsule_digest"] != run["capsule_digest"]
            or regression["vault_revision"] != run["vault_revision"]
            or regression["task_sha256"] != run["task_sha256"]
            or regression["selected_asset_ids"] != run["selected_asset_ids"]
            or regression["source_ids"] != run["source_ids"]
            or regression["expected_helpful_asset_ids"] != payload["helpful_asset_ids"]
            or regression["case_id"]
            != stable_id(
                "case",
                self.vault_id,
                payload["run_id"],
                payload["capsule_digest"],
                canonical_json(payload["helpful_asset_ids"]),
                canonical_json(payload["missing_knowledge"]),
            )
        ):
            raise ValueError("feedback regression case is invalid")
        statement = (
            f"Outcome: {payload['outcome']}\n"
            f"Observation: {payload['observation'].strip()}\n"
            f"Recommended action: {payload['recommended_action'].strip()}\n"
            f"Helpful assets: {', '.join(payload['helpful_asset_ids']) or 'none'}\n"
            f"Missing knowledge: {'; '.join(payload['missing_knowledge']) or 'none'}"
        )
        try:
            self.connection.execute("BEGIN IMMEDIATE")
            self._require_healthy_integrity()
            proposal, inserted = self._insert_asset(
                kind="lesson",
                memory_tier="experience",
                title=f"Run feedback: {payload['outcome']} · {payload['run_id']}",
                statement=statement,
                semantic_key=None,
                status=("quarantined" if has_instruction_risk(statement) else "proposed"),
                verification="unverified",
                trust="user_provided",
                sensitivity=cast(Sensitivity, payload["sensitivity"]),
                source_refs=(),
                tags=("structured-feedback", payload["outcome"]),
                warnings=(
                    ("instruction-like feedback requires explicit review",)
                    if has_instruction_risk(statement)
                    else ()
                ),
                expires_at=None,
                supersedes_asset_id=None,
                origin_uri=f"deeplaw://{self.vault_id}/runs/{payload['run_id']}",
                created_at=created_at,
            )
            if inserted:
                self._append_event(
                    event_type="asset_proposed",
                    object_id=proposal.asset_id,
                    payload={
                        "content_sha256": proposal.content_sha256,
                        "status": proposal.status,
                    },
                )
            body = {**deepcopy(payload), "created_at": created_at}
            body["proposal_asset_id"] = proposal.asset_id
            receipt_sha256 = sha256_bytes(canonical_json(body).encode("utf-8"))
            feedback_id = stable_id("feedback", self.vault_id, receipt_sha256)
            self.connection.execute(
                "INSERT INTO feedback_records VALUES (?, ?, ?, ?, ?, ?)",
                (
                    feedback_id,
                    payload["run_id"],
                    canonical_json(body),
                    receipt_sha256,
                    created_at,
                    proposal.asset_id,
                ),
            )
            revision, audit_head = self._append_event(
                event_type="feedback_recorded",
                object_id=feedback_id,
                payload={
                    "receipt_sha256": receipt_sha256,
                    "run_id": payload["run_id"],
                    "proposal_asset_id": proposal.asset_id,
                },
            )
            self.connection.commit()
        except BaseException:
            self.connection.rollback()
            raise
        return {
            **body,
            "feedback_id": feedback_id,
            "receipt_sha256": receipt_sha256,
            "proposal": proposal.to_dict(),
            "revision": revision,
            "audit_head": audit_head,
            "valid": True,
        }

    def get_feedback(self, feedback_id: str) -> dict[str, Any]:
        self._require_control()
        if not isinstance(feedback_id, str) or not _FEEDBACK_ID.fullmatch(feedback_id):
            raise ValueError("feedback ID is invalid")
        row = self.connection.execute(
            "SELECT * FROM feedback_records WHERE feedback_id = ?",
            (feedback_id,),
        ).fetchone()
        if row is None:
            raise KeyError(f"feedback record is unavailable: {feedback_id}")
        payload = strict_json_loads(row["payload_json"])
        receipt_sha256 = sha256_bytes(canonical_json(payload).encode("utf-8"))
        record_valid = bool(
            receipt_sha256 == row["receipt_sha256"]
            and stable_id("feedback", self.vault_id, receipt_sha256) == feedback_id
            and row["proposal_asset_id"] == payload.get("proposal_asset_id")
        )
        vault_integrity_valid = self.verify_integrity()["valid"]
        return {
            **payload,
            "feedback_id": feedback_id,
            "receipt_sha256": row["receipt_sha256"],
            "record_valid": record_valid,
            "vault_integrity_valid": vault_integrity_valid,
            "valid": bool(record_valid and vault_integrity_valid),
        }

    def list_feedback(self, *, limit: int = 100) -> dict[str, Any]:
        self._require_control()
        if isinstance(limit, bool) or not 1 <= limit <= 500:
            raise ValueError("feedback list limit must be between 1 and 500")
        total = self.connection.execute("SELECT COUNT(*) FROM feedback_records").fetchone()[0]
        rows = self.connection.execute(
            """
            SELECT feedback_id FROM feedback_records
            ORDER BY created_at DESC, feedback_id LIMIT ?
            """,
            (limit,),
        ).fetchall()
        return {
            "schema_version": "deeplaw.knowledge-feedback-list/v1",
            "vault_id": self.vault_id,
            "total": total,
            "feedback": [self.get_feedback(row["feedback_id"]) for row in rows],
            "truncated": len(rows) < total,
        }

    def _approve_asset_in_transaction(
        self,
        asset_id: str,
        *,
        confirm_quarantined: bool,
        source_file_cache: dict[str, dict[str, Any]],
        allow_source_successor: bool = False,
        reviewer_id: str = "local-operator",
        policy_id: str = "deeplaw.local-review/v1",
    ) -> KnowledgeAsset:
        asset = self.get_asset(asset_id, include_inactive=True)
        if asset.status not in {"proposed", "quarantined"}:
            raise ValueError("only proposed or quarantined assets can be approved")
        if asset.status == "quarantined" and not confirm_quarantined:
            raise ValueError(
                "quarantined asset approval requires a separate explicit "
                "quarantine-risk confirmation"
            )
        if asset.expires_at is not None and asset.expires_at <= utc_now():
            raise ValueError("expired working memory cannot be approved")
        invalid_sources = [
            reference.source_id
            for reference in asset.source_refs
            if not self._source_file_check(
                reference.source_id,
                cache=source_file_cache,
            )["valid"]
        ]
        if invalid_sources:
            raise RuntimeError(
                "source-bound knowledge cannot be approved because its stored "
                "source file is missing or has changed"
            )
        if not allow_source_successor:
            for reference in asset.source_refs:
                lifecycle = self.connection.execute(
                    """
                    SELECT status, previous_source_id FROM source_lifecycle
                    WHERE source_id = ?
                    """,
                    (reference.source_id,),
                ).fetchone()
                if (
                    lifecycle is not None
                    and lifecycle["status"] == "pending"
                    and lifecycle["previous_source_id"] is not None
                ):
                    raise ValueError(
                        "a successor source version must be activated through an exact "
                        "source review manifest; individual approval would break atomic update"
                    )
        if asset.semantic_key is not None:
            current = self.connection.execute(
                """
                SELECT asset_id FROM assets
                WHERE semantic_key = ? AND status = 'active'
                """,
                (asset.semantic_key,),
            ).fetchone()
            if current is not None:
                if asset.supersedes_asset_id != current["asset_id"]:
                    raise ValueError(
                        "semantic_key already has an active asset; propose an explicit "
                        "superseding asset instead"
                    )
                self.connection.execute(
                    "UPDATE assets SET status = 'superseded' WHERE asset_id = ?",
                    (current["asset_id"],),
                )
        if asset.supersedes_asset_id is not None:
            superseded = self.connection.execute(
                "SELECT status, semantic_key FROM assets WHERE asset_id = ?",
                (asset.supersedes_asset_id,),
            ).fetchone()
            if superseded is None or superseded["status"] not in {
                "active",
                "superseded",
            }:
                raise ValueError("superseded asset is missing or not reviewable")
            if superseded["semantic_key"] != asset.semantic_key:
                raise ValueError("superseded asset semantic_key mismatch")
            self.connection.execute(
                "UPDATE assets SET status = 'superseded' WHERE asset_id = ?",
                (asset.supersedes_asset_id,),
            )
        activated_at = utc_now()
        self.connection.execute(
            """
            UPDATE assets
            SET status = 'active', verification = 'human_verified', activated_at = ?
            WHERE asset_id = ?
            """,
            (activated_at, asset_id),
        )
        self._append_event(
            event_type="asset_approved",
            object_id=asset_id,
            payload={
                "content_sha256": asset.content_sha256,
                "supersedes_asset_id": asset.supersedes_asset_id,
            },
        )
        if self.identity_v2_enabled:
            identity = self.connection.execute(
                """
                SELECT asset_revision_bindings_v2.asset_revision_id
                FROM asset_revision_bindings_v2
                WHERE legacy_asset_id = ?
                """,
                (asset_id,),
            ).fetchone()
            if identity is not None:
                record_governance_revision(
                    self.connection,
                    subject_kind="asset_revision",
                    subject_id=identity["asset_revision_id"],
                    trust=asset.trust,
                    sensitivity=asset.sensitivity,
                    policy_id=policy_id,
                    review_status="human_verified",
                    lifecycle_status="active",
                    activation_status="active",
                    export_allowed=asset.sensitivity == "public",
                    reviewer_id=reviewer_id,
                    recorded_at=activated_at,
                )
            if asset.supersedes_asset_id is not None:
                predecessor = self.connection.execute(
                    """
                    SELECT asset_revision_bindings_v2.asset_revision_id,
                           assets.trust, assets.sensitivity
                    FROM asset_revision_bindings_v2
                    JOIN assets
                      ON assets.asset_id = asset_revision_bindings_v2.legacy_asset_id
                    WHERE asset_revision_bindings_v2.legacy_asset_id = ?
                    """,
                    (asset.supersedes_asset_id,),
                ).fetchone()
                if predecessor is not None:
                    record_governance_revision(
                        self.connection,
                        subject_kind="asset_revision",
                        subject_id=predecessor["asset_revision_id"],
                        trust=predecessor["trust"],
                        sensitivity=predecessor["sensitivity"],
                        policy_id=policy_id,
                        review_status="human_verified",
                        lifecycle_status="superseded",
                        activation_status="inactive",
                        export_allowed=False,
                        reviewer_id=reviewer_id,
                        recorded_at=activated_at,
                    )
        return self.get_asset(asset_id, include_inactive=True)

    def approve_asset(
        self,
        asset_id: str,
        *,
        confirm_reviewed: bool,
        confirm_quarantined: bool = False,
        reviewer_id: str = "local-operator",
        review_reason: str = "Explicit human review completed.",
        policy_id: str = "deeplaw.local-review/v1",
    ) -> KnowledgeAsset:
        self._require_write()
        self._require_control()
        if not confirm_reviewed:
            raise ValueError("asset approval requires explicit reviewed confirmation")
        try:
            self.connection.execute("BEGIN IMMEDIATE")
            self._require_healthy_integrity()
            proposal = self.get_asset(asset_id, include_inactive=True)
            manifest_body = {
                "schema_version": "deeplaw.knowledge-review-manifest/v1",
                "vault_id": self.vault_id,
                "source_id": None,
                "asset_id": proposal.asset_id,
                "content_sha256": proposal.content_sha256,
                "status": proposal.status,
            }
            manifest_sha256 = sha256_bytes(canonical_json(manifest_body).encode("utf-8"))
            approved = self._approve_asset_in_transaction(
                asset_id,
                confirm_quarantined=confirm_quarantined,
                source_file_cache={},
                reviewer_id=reviewer_id,
                policy_id=policy_id,
            )
            self._record_review_receipt(
                assets=[proposal],
                source_id=None,
                reviewer_id=reviewer_id,
                policy_id=policy_id,
                reason=review_reason,
                review_manifest_sha256=manifest_sha256,
            )
            identity_row = self.connection.execute(
                """
                SELECT source_revision_bindings_v2.source_revision_id
                FROM asset_revision_bindings_v2
                JOIN source_revision_bindings_v2 USING(legacy_source_id)
                WHERE asset_revision_bindings_v2.legacy_asset_id = ?
                """,
                (asset_id,),
            ).fetchone()
            if identity_row is not None:
                self._append_identity_snapshot(
                    reason="governance_recorded",
                    source_revision_id=identity_row["source_revision_id"],
                )
            self.connection.commit()
            return approved
        except BaseException:
            self.connection.rollback()
            raise

    def approve_source_assets(
        self,
        source_id: str,
        *,
        confirm_reviewed: bool,
        confirm_quarantined: bool = False,
        review_manifest_sha256: str,
        reviewer_id: str = "local-operator",
        review_reason: str = "The exact compiled source manifest was reviewed.",
        policy_id: str = "deeplaw.local-source-review/v1",
    ) -> dict[str, Any]:
        """Atomically approve one reviewed compiled source without N integrity replays."""
        self._require_write()
        self._require_control()
        if not confirm_reviewed:
            raise ValueError("source approval requires explicit reviewed confirmation")
        if not isinstance(source_id, str) or not _SOURCE_ID.fullmatch(source_id):
            raise ValueError("knowledge source ID is invalid")
        if not isinstance(review_manifest_sha256, str) or not _SHA256.fullmatch(
            review_manifest_sha256
        ):
            raise ValueError("source approval requires an exact review manifest SHA-256")
        try:
            self.connection.execute("BEGIN IMMEDIATE")
            self._require_healthy_integrity()
            source = self.connection.execute(
                "SELECT source_id FROM sources WHERE source_id = ?",
                (source_id,),
            ).fetchone()
            if source is None:
                raise KeyError(f"knowledge source is unavailable: {source_id}")
            if not self._source_file_check(source_id)["valid"]:
                raise RuntimeError(
                    "compiled source cannot be approved because its stored source "
                    "file is missing or has changed"
                )
            manifest = self.source_review_manifest(source_id)
            if review_manifest_sha256 != manifest["review_manifest_sha256"]:
                raise RuntimeError(
                    "review manifest changed; inspect the exact current source membership"
                )
            source_info = self.source_info(source_id)
            if source_info.get("proposal_set_id") is not None:
                rows = self.connection.execute(
                    """
                    SELECT assets.asset_id, assets.status
                    FROM asset_revision_bindings_v2
                    JOIN assets
                      ON assets.asset_id = asset_revision_bindings_v2.legacy_asset_id
                    WHERE asset_revision_bindings_v2.legacy_source_id = ?
                      AND asset_revision_bindings_v2.proposal_set_id = ?
                    ORDER BY asset_revision_bindings_v2.proposal_ordinal
                    """,
                    (source_id, source_info["proposal_set_id"]),
                ).fetchall()
            else:
                rows = self.connection.execute(
                    """
                    SELECT DISTINCT assets.asset_id, assets.status
                    FROM assets, json_each(assets.source_refs_json) AS reference
                    WHERE json_extract(reference.value, '$.source_id') = ?
                      AND json_array_length(assets.source_refs_json) = 1
                    ORDER BY assets.asset_id
                    """,
                    (source_id,),
                ).fetchall()
            fragment_count = self.connection.execute(
                "SELECT COUNT(*) FROM source_fragments WHERE source_id = ?",
                (source_id,),
            ).fetchone()[0]
            if source_info.get("proposal_set_id") is None and (
                not rows or len(rows) != fragment_count
            ):
                raise RuntimeError(
                    "compiled knowledge source membership is incomplete or ambiguous"
                )
            if len(rows) > _MAX_BATCH_APPROVAL_ASSETS:
                raise ValueError("source approval exceeds the 100000-asset atomic review bound")
            unsupported = [
                row["asset_id"]
                for row in rows
                if row["status"] not in {"proposed", "quarantined", "active"}
            ]
            if unsupported:
                raise ValueError(
                    "compiled source contains revoked or superseded assets and cannot "
                    "be batch-approved"
                )
            source_file_cache: dict[str, dict[str, Any]] = {}
            approved_ids: list[str] = []
            reviewed_assets: list[KnowledgeAsset] = []
            already_active = 0
            for row in rows:
                reviewed_assets.append(self.get_asset(row["asset_id"], include_inactive=True))
                if row["status"] == "active":
                    already_active += 1
                    continue
                approved = self._approve_asset_in_transaction(
                    row["asset_id"],
                    confirm_quarantined=confirm_quarantined,
                    source_file_cache=source_file_cache,
                    allow_source_successor=True,
                    reviewer_id=reviewer_id,
                    policy_id=policy_id,
                )
                approved_ids.append(approved.asset_id)
            activated_source = False
            revoked_ids: list[str] = []
            if (
                source_info["status"] == "pending"
                and len(rows) == len(approved_ids) + already_active
            ):
                previous_source_id = source_info["previous_source_id"]
                current = self.active_source_for_key(source_info["source_key"])
                if current is not None and current["source_id"] != previous_source_id:
                    raise RuntimeError(
                        "source activation is stale because the logical source changed"
                    )
                if previous_source_id is not None:
                    remaining = self.connection.execute(
                        """
                        SELECT DISTINCT assets.asset_id, assets.content_sha256
                        FROM assets, json_each(assets.source_refs_json) AS reference
                        WHERE json_extract(reference.value, '$.source_id') = ?
                          AND assets.status = 'active'
                        ORDER BY assets.asset_id
                        """,
                        (previous_source_id,),
                    ).fetchall()
                    for stale in remaining:
                        stale_identity = self.connection.execute(
                            """
                            SELECT asset_revision_bindings_v2.asset_revision_id,
                                   knowledge_revisions_v2.knowledge_key,
                                   assets.trust, assets.sensitivity
                            FROM asset_revision_bindings_v2
                            JOIN knowledge_revisions_v2 USING(asset_revision_id)
                            JOIN assets
                              ON assets.asset_id = asset_revision_bindings_v2.legacy_asset_id
                            WHERE asset_revision_bindings_v2.legacy_asset_id = ?
                            """,
                            (stale["asset_id"],),
                        ).fetchone()
                        self.connection.execute(
                            "UPDATE assets SET status = 'revoked' WHERE asset_id = ?",
                            (stale["asset_id"],),
                        )
                        self._append_event(
                            event_type="asset_revoked",
                            object_id=stale["asset_id"],
                            payload={
                                "reason": (
                                    "Source version was replaced and this section "
                                    "is absent from the reviewed successor."
                                ),
                                "content_sha256": stale["content_sha256"],
                            },
                        )
                        if (
                            stale_identity is not None
                            and source_info.get("source_revision_id") is not None
                        ):
                            recorded_at = utc_now()
                            record_lineage_transition(
                                self.connection,
                                knowledge_key=stale_identity["knowledge_key"],
                                from_asset_revision_ids=(stale_identity["asset_revision_id"],),
                                to_asset_revision_ids=(),
                                status="deleted",
                                source_revision_id=source_info["source_revision_id"],
                                mapping_evidence={
                                    "method": "reviewed-successor-absence",
                                    "previous_source_id": previous_source_id,
                                    "successor_source_id": source_id,
                                },
                                created_at=recorded_at,
                            )
                            record_governance_revision(
                                self.connection,
                                subject_kind="asset_revision",
                                subject_id=stale_identity["asset_revision_id"],
                                trust=stale_identity["trust"],
                                sensitivity=stale_identity["sensitivity"],
                                policy_id=policy_id,
                                review_status="human_verified",
                                lifecycle_status="deleted",
                                activation_status="inactive",
                                revoked_at=recorded_at,
                                export_allowed=False,
                                reviewer_id=reviewer_id,
                                recorded_at=recorded_at,
                            )
                        revoked_ids.append(stale["asset_id"])
                    switched_at = utc_now()
                    self.connection.execute(
                        """
                        UPDATE source_lifecycle
                        SET status = 'superseded', superseded_at = ?
                        WHERE source_id = ? AND status = 'active'
                        """,
                        (switched_at, previous_source_id),
                    )
                    previous_source_identity = self.connection.execute(
                        """
                        SELECT source_revision_bindings_v2.source_revision_id,
                               sources.trust, sources.sensitivity
                        FROM source_revision_bindings_v2
                        JOIN sources
                          ON sources.source_id =
                             source_revision_bindings_v2.legacy_source_id
                        WHERE source_revision_bindings_v2.legacy_source_id = ?
                        """,
                        (previous_source_id,),
                    ).fetchone()
                    if previous_source_identity is not None:
                        previous_source_governance = self.connection.execute(
                            """
                            SELECT recorded_at FROM governance_revisions_v2
                            WHERE subject_kind = 'source_revision' AND subject_id = ?
                            ORDER BY recorded_at DESC, governance_revision DESC LIMIT 1
                            """,
                            (previous_source_identity["source_revision_id"],),
                        ).fetchone()
                        governance_recorded_at = (
                            _timestamp_after(
                                switched_at,
                                previous_source_governance["recorded_at"],
                            )
                            if previous_source_governance is not None
                            else switched_at
                        )
                        record_governance_revision(
                            self.connection,
                            subject_kind="source_revision",
                            subject_id=previous_source_identity["source_revision_id"],
                            trust=previous_source_identity["trust"],
                            sensitivity=previous_source_identity["sensitivity"],
                            policy_id=policy_id,
                            review_status="human_verified",
                            lifecycle_status="superseded",
                            activation_status="inactive",
                            revoked_at=switched_at,
                            export_allowed=False,
                            reviewer_id=reviewer_id,
                            recorded_at=governance_recorded_at,
                        )
                else:
                    switched_at = utc_now()
                self.connection.execute(
                    """
                    UPDATE source_lifecycle
                    SET status = 'active', activated_at = ?
                    WHERE source_id = ? AND status = 'pending'
                    """,
                    (switched_at, source_id),
                )
                if source_info.get("source_revision_id") is not None:
                    record_governance_revision(
                        self.connection,
                        subject_kind="source_revision",
                        subject_id=source_info["source_revision_id"],
                        trust=source_info["trust"],
                        sensitivity=source_info["sensitivity"],
                        policy_id=policy_id,
                        review_status="human_verified",
                        lifecycle_status="active",
                        activation_status="active",
                        export_allowed=source_info["sensitivity"] == "public",
                        reviewer_id=reviewer_id,
                        recorded_at=switched_at,
                    )
                self._append_event(
                    event_type="source_activated",
                    object_id=source_id,
                    payload={
                        "source_key": source_info["source_key"],
                        "previous_source_id": previous_source_id,
                        "activated_at": switched_at,
                        "revoked_asset_count": len(revoked_ids),
                        "revoked_assets_sha256": sha256_bytes(
                            canonical_json(revoked_ids).encode("utf-8")
                        ),
                    },
                )
                activated_source = True
            receipt = self._record_review_receipt(
                assets=reviewed_assets,
                source_id=source_id,
                reviewer_id=reviewer_id,
                policy_id=policy_id,
                reason=review_reason,
                review_manifest_sha256=manifest["review_manifest_sha256"],
            )
            if source_info.get("source_revision_id") is not None:
                self._append_identity_snapshot(
                    reason="governance_recorded",
                    source_revision_id=source_info["source_revision_id"],
                )
            self.connection.commit()
        except BaseException:
            self.connection.rollback()
            raise
        visible_ids = approved_ids[:100]
        return {
            "schema_version": "deeplaw.knowledge-source-approval/v1",
            "vault_id": self.vault_id,
            "source_id": source_id,
            "reviewed_asset_count": len(rows),
            "approved_asset_count": len(approved_ids),
            "already_active_asset_count": already_active,
            "approved_asset_ids": visible_ids,
            "approved_asset_ids_truncated": len(visible_ids) < len(approved_ids),
            "review_manifest_sha256": manifest["review_manifest_sha256"],
            "review_receipt": receipt,
            "source_activated": activated_source,
            "revoked_prior_asset_count": len(revoked_ids),
            "revision": self.revision,
            "audit_head": self.audit_head,
        }

    def revoke_asset(
        self,
        asset_id: str,
        *,
        reason: str,
        confirm: bool,
    ) -> KnowledgeAsset:
        self._require_write()
        if not confirm:
            raise ValueError("asset revocation requires explicit confirmation")
        reason = reason.strip()
        if not reason or len(reason) > 2_000:
            raise ValueError("revocation reason must be between 1 and 2000 characters")
        try:
            self.connection.execute("BEGIN IMMEDIATE")
            self._require_healthy_integrity()
            asset = self.get_asset(asset_id, include_inactive=True)
            if asset.status == "revoked":
                self.connection.rollback()
                return asset
            self.connection.execute(
                "UPDATE assets SET status = 'revoked' WHERE asset_id = ?",
                (asset_id,),
            )
            self._append_event(
                event_type="asset_revoked",
                object_id=asset_id,
                payload={"reason": reason, "content_sha256": asset.content_sha256},
            )
            identity_row = self.connection.execute(
                """
                SELECT asset_revision_bindings_v2.asset_revision_id,
                       source_revision_bindings_v2.source_revision_id
                FROM asset_revision_bindings_v2
                JOIN source_revision_bindings_v2 USING(legacy_source_id)
                WHERE asset_revision_bindings_v2.legacy_asset_id = ?
                """,
                (asset_id,),
            ).fetchone()
            if identity_row is not None:
                revoked_at = utc_now()
                record_governance_revision(
                    self.connection,
                    subject_kind="asset_revision",
                    subject_id=identity_row["asset_revision_id"],
                    trust=asset.trust,
                    sensitivity=asset.sensitivity,
                    policy_id="deeplaw.local-revocation/v2",
                    review_status="human_verified",
                    lifecycle_status="revoked",
                    activation_status="inactive",
                    revoked_at=revoked_at,
                    export_allowed=False,
                    reviewer_id="local-operator",
                    recorded_at=revoked_at,
                )
                self._append_identity_snapshot(
                    reason="governance_recorded",
                    source_revision_id=identity_row["source_revision_id"],
                )
            self.connection.commit()
            return self.get_asset(asset_id, include_inactive=True)
        except BaseException:
            self.connection.rollback()
            raise

    def selectively_forget(
        self,
        *,
        knowledge_key: str | None = None,
        asset_id: str | None = None,
        reason: str,
        confirm: bool,
    ) -> dict[str, Any]:
        """Revoke one current knowledge revision while retaining verifiable history."""
        self._require_write()
        if (knowledge_key is None) == (asset_id is None):
            raise ValueError("selective forgetting requires exactly one knowledge key or asset ID")
        if not confirm:
            raise ValueError("selective forgetting requires explicit confirmation")
        reason = reason.strip()
        if not reason or len(reason) > 2_000:
            raise ValueError("forgetting reason must be between 1 and 2000 characters")
        if knowledge_key is not None and not re.fullmatch(
            r"knowledge_[0-9a-f]{24}", knowledge_key
        ):
            raise ValueError("knowledge key is invalid")
        if asset_id is not None and not _ASSET_ID.fullmatch(asset_id):
            raise ValueError("knowledge asset ID is invalid")
        if not self.identity_v2_enabled:
            raise RuntimeError("selective forgetting requires Knowledge Identity v2")

        if asset_id is not None:
            binding = self.connection.execute(
                """
                SELECT knowledge_revisions_v2.knowledge_key,
                       asset_revision_bindings_v2.asset_revision_id,
                       assets.status
                FROM asset_revision_bindings_v2
                JOIN knowledge_revisions_v2 USING(asset_revision_id)
                JOIN assets
                  ON assets.asset_id = asset_revision_bindings_v2.legacy_asset_id
                WHERE asset_revision_bindings_v2.legacy_asset_id = ?
                """,
                (asset_id,),
            ).fetchone()
            if binding is None:
                legacy_asset = self.connection.execute(
                    "SELECT asset_id, status FROM assets WHERE asset_id = ?",
                    (asset_id,),
                ).fetchone()
                if legacy_asset is None:
                    raise KeyError(f"knowledge asset is unavailable: {asset_id}")
                target_asset_id = asset_id
                target_revision_id = None
                already_inactive = legacy_asset["status"] != "active"
                identity_model = "legacy-unbound"
            else:
                knowledge_key = binding["knowledge_key"]
                target_asset_id = asset_id
                target_revision_id = binding["asset_revision_id"]
                already_inactive = binding["status"] != "active"
                identity_model = "knowledge-identity-v2"
        else:
            identity_model = "knowledge-identity-v2"
            rows = self.connection.execute(
                """
                SELECT asset_revision_bindings_v2.legacy_asset_id,
                       asset_revision_bindings_v2.asset_revision_id
                FROM knowledge_revisions_v2
                JOIN asset_revision_bindings_v2 USING(asset_revision_id)
                JOIN assets
                  ON assets.asset_id = asset_revision_bindings_v2.legacy_asset_id
                WHERE knowledge_revisions_v2.knowledge_key = ?
                  AND assets.status = 'active'
                ORDER BY asset_revision_bindings_v2.observed_at DESC,
                         asset_revision_bindings_v2.legacy_asset_id
                """,
                (knowledge_key,),
            ).fetchall()
            if len(rows) > 1:
                raise RuntimeError(
                    "knowledge key has multiple active revisions; forgetting stopped"
                )
            if not rows:
                historical = self.connection.execute(
                    """
                    SELECT asset_revision_bindings_v2.legacy_asset_id,
                           asset_revision_bindings_v2.asset_revision_id
                    FROM knowledge_revisions_v2
                    JOIN asset_revision_bindings_v2 USING(asset_revision_id)
                    WHERE knowledge_revisions_v2.knowledge_key = ?
                    ORDER BY asset_revision_bindings_v2.observed_at DESC,
                             asset_revision_bindings_v2.legacy_asset_id
                    LIMIT 1
                    """,
                    (knowledge_key,),
                ).fetchone()
                if historical is None:
                    raise KeyError(f"knowledge key is unavailable: {knowledge_key}")
                target_asset_id = historical["legacy_asset_id"]
                target_revision_id = historical["asset_revision_id"]
                already_inactive = True
            else:
                target_asset_id = rows[0]["legacy_asset_id"]
                target_revision_id = rows[0]["asset_revision_id"]
                already_inactive = False

        if not already_inactive:
            self.revoke_asset(target_asset_id, reason=reason, confirm=True)
        if knowledge_key is None:
            current_relation_count = self.connection.execute(
                """
                SELECT COUNT(*) AS relation_count
                FROM relations
                JOIN assets AS subject_asset
                  ON subject_asset.asset_id = relations.subject_asset_id
                JOIN assets AS object_asset
                  ON object_asset.asset_id = relations.object_asset_id
                WHERE subject_asset.status = 'active' AND object_asset.status = 'active'
                  AND (relations.subject_asset_id = ? OR relations.object_asset_id = ?)
                """,
                (target_asset_id, target_asset_id),
            ).fetchone()["relation_count"]
        else:
            current_relation_count = self.connection.execute(
                """
                WITH latest AS (
                    SELECT relation_revisions_v2.*,
                           ROW_NUMBER() OVER (
                               PARTITION BY relation_key
                               ORDER BY observed_at DESC, relation_revision_id DESC
                           ) AS relation_rank
                    FROM relation_revisions_v2
                )
                SELECT COUNT(*) AS relation_count
                FROM latest
                JOIN asset_revision_bindings_v2 AS subject_binding
                  ON subject_binding.asset_revision_id = latest.subject_asset_revision_id
                JOIN assets AS subject_asset
                  ON subject_asset.asset_id = subject_binding.legacy_asset_id
                JOIN asset_revision_bindings_v2 AS object_binding
                  ON object_binding.asset_revision_id = latest.object_asset_revision_id
                JOIN assets AS object_asset
                  ON object_asset.asset_id = object_binding.legacy_asset_id
                WHERE latest.relation_rank = 1 AND latest.status = 'active'
                  AND subject_asset.status = 'active' AND object_asset.status = 'active'
                  AND (latest.subject_knowledge_key = ? OR latest.object_knowledge_key = ?)
                """,
                (knowledge_key, knowledge_key),
            ).fetchone()["relation_count"]
        return {
            "schema_version": "deeplaw.selective-forgetting/v1",
            "vault_id": self.vault_id,
            "knowledge_key": knowledge_key,
            "asset_id": target_asset_id,
            "asset_revision_id": target_revision_id,
            "identity_model": identity_model,
            "already_inactive": already_inactive,
            "current_relation_count": current_relation_count,
            "current_retrieval_eligible": False,
            "history_retained": True,
            "canonical_bytes_deleted": False,
            "revision": self.revision,
            "audit_head": self.audit_head,
        }

    def add_relation(
        self,
        *,
        subject_asset_id: str,
        predicate: str,
        object_asset_id: str,
        evidence_fragment_id: str | None = None,
        confirm_reviewed: bool,
        event_time: str | None = None,
        valid_from: str | None = None,
        valid_to: str | None = None,
    ) -> dict[str, Any]:
        self._require_write()
        if not confirm_reviewed:
            raise ValueError("knowledge relation requires explicit reviewed confirmation")
        if predicate not in RELATION_PREDICATES:
            raise ValueError(f"unsupported knowledge relation predicate: {predicate}")
        if subject_asset_id == object_asset_id:
            raise ValueError("knowledge relation cannot be a self-loop")
        try:
            self.connection.execute("BEGIN IMMEDIATE")
            self._require_healthy_integrity()
            subject = self.get_asset(subject_asset_id, include_inactive=False)
            target = self.get_asset(object_asset_id, include_inactive=False)
            if evidence_fragment_id is not None:
                fragment = (
                    self.connection.execute(
                        """
                        SELECT source_fragments.fragment_id, source_lifecycle.status
                        FROM source_fragments
                        JOIN source_lifecycle USING(source_id)
                        WHERE source_fragments.fragment_id = ?
                        """,
                        (evidence_fragment_id,),
                    ).fetchone()
                    if self.control_enabled
                    else self.connection.execute(
                        "SELECT fragment_id, NULL AS status FROM source_fragments "
                        "WHERE fragment_id = ?",
                        (evidence_fragment_id,),
                    ).fetchone()
                )
                if fragment is None:
                    raise ValueError("relation evidence fragment does not exist")
                if self.control_enabled and fragment["status"] != "active":
                    raise ValueError("relation evidence source must be active and reviewed")
            relation_id = stable_id(
                "relation",
                self.vault_id,
                subject.asset_id,
                predicate,
                target.asset_id,
                evidence_fragment_id or "",
            )
            existing = self.connection.execute(
                "SELECT * FROM relations WHERE relation_id = ?",
                (relation_id,),
            ).fetchone()
            if existing is not None:
                identity_relation = self.connection.execute(
                    """
                    SELECT relation_key, relation_revision_id
                    FROM relation_revisions_v2 WHERE legacy_relation_id = ?
                    """,
                    (relation_id,),
                ).fetchone()
                self.connection.rollback()
                return {
                    "relation_id": existing["relation_id"],
                    "subject_uri": subject.uri,
                    "predicate": existing["predicate"],
                    "object_uri": target.uri,
                    "evidence_fragment_id": existing["evidence_fragment_id"],
                    "verification": existing["verification"],
                    "created_at": existing["created_at"],
                    "relation_key": (
                        identity_relation["relation_key"] if identity_relation is not None else None
                    ),
                    "relation_revision_id": (
                        identity_relation["relation_revision_id"]
                        if identity_relation is not None
                        else None
                    ),
                }
            conflicting = self.connection.execute(
                """
                SELECT relation_id FROM relations
                WHERE subject_asset_id = ? AND predicate = ? AND object_asset_id = ?
                """,
                (subject.asset_id, predicate, target.asset_id),
            ).fetchone()
            if conflicting is not None:
                self.connection.rollback()
                raise ValueError("knowledge relation already exists with different evidence")
            created_at = utc_now()
            self.connection.execute(
                "INSERT INTO relations VALUES (?, ?, ?, ?, ?, ?, ?)",
                (
                    relation_id,
                    subject.asset_id,
                    predicate,
                    target.asset_id,
                    evidence_fragment_id,
                    "human_verified",
                    created_at,
                ),
            )
            identity_relation: dict[str, str] | None = None
            endpoint_identities = self.connection.execute(
                """
                SELECT subject_binding.asset_revision_id AS subject_revision_id,
                       subject_revision.knowledge_key AS subject_knowledge_key,
                       object_binding.asset_revision_id AS object_revision_id,
                       object_revision.knowledge_key AS object_knowledge_key
                FROM asset_revision_bindings_v2 AS subject_binding
                JOIN knowledge_revisions_v2 AS subject_revision
                  ON subject_revision.asset_revision_id =
                     subject_binding.asset_revision_id
                JOIN asset_revision_bindings_v2 AS object_binding
                  ON object_binding.legacy_asset_id = ?
                JOIN knowledge_revisions_v2 AS object_revision
                  ON object_revision.asset_revision_id = object_binding.asset_revision_id
                WHERE subject_binding.legacy_asset_id = ?
                """,
                (target.asset_id, subject.asset_id),
            ).fetchone()
            if endpoint_identities is not None:
                if evidence_fragment_id is None:
                    raise ValueError(
                        "Identity v2 relations require an exact source evidence fragment"
                    )
                evidence = self.connection.execute(
                    """
                    SELECT source_revision_bindings_v2.source_revision_id,
                           legacy_fragment_bindings_v2.fragment_revision_id,
                           source_fragments.locator,
                           source_fragments.text_sha256,
                           sources.sensitivity
                    FROM legacy_fragment_bindings_v2
                    JOIN source_fragments USING(fragment_id)
                    JOIN source_revision_bindings_v2 USING(legacy_source_id)
                    JOIN sources
                      ON sources.source_id =
                         source_revision_bindings_v2.legacy_source_id
                    WHERE legacy_fragment_bindings_v2.fragment_id = ?
                    """,
                    (evidence_fragment_id,),
                ).fetchone()
                if evidence is None:
                    raise ValueError("relation evidence lacks an Identity v2 binding")
                identity_relation = record_relation_revision(
                    self.connection,
                    vault_id=self.vault_id,
                    legacy_relation_id=relation_id,
                    subject_knowledge_key=endpoint_identities["subject_knowledge_key"],
                    object_knowledge_key=endpoint_identities["object_knowledge_key"],
                    subject_asset_revision_id=endpoint_identities["subject_revision_id"],
                    object_asset_revision_id=endpoint_identities["object_revision_id"],
                    predicate=predicate,
                    evidence_refs=[
                        {
                            "source_revision_id": evidence["source_revision_id"],
                            "fragment_revision_id": evidence["fragment_revision_id"],
                            "locator": evidence["locator"],
                            "quote_sha256": evidence["text_sha256"],
                        }
                    ],
                    status="active",
                    event_time=event_time,
                    valid_from=valid_from,
                    valid_to=valid_to,
                    observed_at=created_at,
                    reviewed_at=created_at,
                    ingest_time=created_at,
                )
                record_governance_revision(
                    self.connection,
                    subject_kind="relation_revision",
                    subject_id=identity_relation["relation_revision_id"],
                    trust="user_provided",
                    sensitivity=_maximum_sensitivity(
                        subject.sensitivity,
                        target.sensitivity,
                        evidence["sensitivity"],
                    ),
                    policy_id="deeplaw.local-relation-review/v2",
                    review_status="human_verified",
                    lifecycle_status="active",
                    activation_status="active",
                    export_allowed=(subject.sensitivity == target.sensitivity == "public"),
                    reviewer_id="local-operator",
                    recorded_at=created_at,
                )
            self._append_event(
                event_type="relation_added",
                object_id=relation_id,
                payload={
                    "subject_asset_id": subject.asset_id,
                    "predicate": predicate,
                    "object_asset_id": target.asset_id,
                    "evidence_fragment_id": evidence_fragment_id,
                },
            )
            if identity_relation is not None:
                self._append_identity_snapshot(
                    reason="relation_recorded",
                    source_revision_id=evidence["source_revision_id"],
                )
            self.connection.commit()
        except BaseException:
            self.connection.rollback()
            raise
        return {
            "relation_id": relation_id,
            "subject_uri": subject.uri,
            "predicate": predicate,
            "object_uri": target.uri,
            "evidence_fragment_id": evidence_fragment_id,
            "verification": "human_verified",
            "created_at": created_at,
            "relation_key": (
                identity_relation["relation_key"] if identity_relation is not None else None
            ),
            "relation_revision_id": (
                identity_relation["relation_revision_id"] if identity_relation is not None else None
            ),
        }

    def revise_temporal_relation(
        self,
        relation_key: str,
        *,
        status: Literal["active", "superseded", "revoked", "ambiguous"],
        evidence_fragment_id: str,
        confirm_reviewed: bool,
        event_time: str | None = None,
        valid_from: str | None = None,
        valid_to: str | None = None,
    ) -> dict[str, Any]:
        self._require_write()
        if not confirm_reviewed:
            raise ValueError("temporal relation revision requires explicit review")
        if not re.fullmatch(r"relationkey_[0-9a-f]{24}", relation_key):
            raise ValueError("relation key is invalid")
        if status not in {"active", "superseded", "revoked", "ambiguous"}:
            raise ValueError("temporal relation status is invalid")
        try:
            self.connection.execute("BEGIN IMMEDIATE")
            self._require_healthy_integrity()
            previous = self.connection.execute(
                """
                SELECT * FROM relation_revisions_v2
                WHERE relation_key = ?
                ORDER BY observed_at DESC, relation_revision_id DESC LIMIT 1
                """,
                (relation_key,),
            ).fetchone()
            if previous is None:
                raise KeyError(f"temporal relation is unavailable: {relation_key}")
            evidence = self.connection.execute(
                """
                SELECT source_revision_bindings_v2.source_revision_id,
                       legacy_fragment_bindings_v2.fragment_revision_id,
                       source_fragments.locator, source_fragments.text_sha256,
                       sources.sensitivity, source_lifecycle.status
                FROM legacy_fragment_bindings_v2
                JOIN source_fragments USING(fragment_id)
                JOIN source_revision_bindings_v2 USING(legacy_source_id)
                JOIN sources
                  ON sources.source_id = source_revision_bindings_v2.legacy_source_id
                JOIN source_lifecycle
                  ON source_lifecycle.source_id =
                     source_revision_bindings_v2.legacy_source_id
                WHERE legacy_fragment_bindings_v2.fragment_id = ?
                """,
                (evidence_fragment_id,),
            ).fetchone()
            if evidence is None:
                raise ValueError("relation evidence lacks an Identity v2 binding")
            if evidence["status"] != "active":
                raise ValueError("relation evidence source must be active and reviewed")
            observed_at = utc_now()
            if observed_at <= previous["observed_at"]:
                previous_time = datetime.fromisoformat(
                    previous["observed_at"].replace("Z", "+00:00")
                )
                observed_at = (
                    (previous_time + timedelta(seconds=1))
                    .astimezone(UTC)
                    .isoformat(timespec="seconds")
                    .replace("+00:00", "Z")
                )
            relation = record_relation_revision(
                self.connection,
                vault_id=self.vault_id,
                legacy_relation_id=None,
                subject_knowledge_key=previous["subject_knowledge_key"],
                object_knowledge_key=previous["object_knowledge_key"],
                subject_asset_revision_id=previous["subject_asset_revision_id"],
                object_asset_revision_id=previous["object_asset_revision_id"],
                predicate=previous["predicate"],
                evidence_refs=[
                    {
                        "source_revision_id": evidence["source_revision_id"],
                        "fragment_revision_id": evidence["fragment_revision_id"],
                        "locator": evidence["locator"],
                        "quote_sha256": evidence["text_sha256"],
                    }
                ],
                status=status,
                event_time=event_time,
                valid_from=valid_from,
                valid_to=valid_to,
                observed_at=observed_at,
                reviewed_at=observed_at,
                ingest_time=observed_at,
            )
            previous_governance = self.connection.execute(
                """
                SELECT sensitivity FROM governance_revisions_v2
                WHERE subject_kind = 'relation_revision' AND subject_id = ?
                ORDER BY recorded_at DESC, governance_revision DESC LIMIT 1
                """,
                (previous["relation_revision_id"],),
            ).fetchone()
            record_governance_revision(
                self.connection,
                subject_kind="relation_revision",
                subject_id=relation["relation_revision_id"],
                trust="user_provided",
                sensitivity=_maximum_sensitivity(
                    (
                        previous_governance["sensitivity"]
                        if previous_governance is not None
                        else "private"
                    ),
                    evidence["sensitivity"],
                ),
                policy_id="deeplaw.local-relation-review/v2",
                review_status="human_verified",
                lifecycle_status=status,
                activation_status="active" if status == "active" else "inactive",
                revoked_at=observed_at if status == "revoked" else None,
                export_allowed=False,
                reviewer_id="local-operator",
                recorded_at=observed_at,
            )
            revision, audit_head = self._append_identity_snapshot(
                reason="relation_recorded",
                source_revision_id=evidence["source_revision_id"],
            )
            self.connection.commit()
        except BaseException:
            self.connection.rollback()
            raise
        return {
            "schema_version": "deeplaw.temporal-relation-revision/v1",
            "vault_id": self.vault_id,
            "relation_key": relation_key,
            "previous_relation_revision_id": previous["relation_revision_id"],
            "relation_revision_id": relation["relation_revision_id"],
            "status": status,
            "event_time": event_time,
            "valid_from": valid_from,
            "valid_to": valid_to,
            "observed_at": observed_at,
            "revision": revision,
            "audit_head": audit_head,
        }

    def _row_to_asset(self, row: sqlite3.Row) -> KnowledgeAsset:
        references_payload = strict_json_loads(row["source_refs_json"])
        references = tuple(
            SourceReference(
                source_id=item["source_id"],
                fragment_id=item["fragment_id"],
                locator=item["locator"],
                quote_sha256=item["quote_sha256"],
            )
            for item in references_payload
        )
        return KnowledgeAsset(
            asset_id=row["asset_id"],
            vault_id=self.vault_id,
            kind=cast(AssetKind, row["kind"]),
            memory_tier=cast(MemoryTier, row["memory_tier"]),
            title=row["title"],
            statement=row["statement"],
            semantic_key=row["semantic_key"],
            status=cast(AssetStatus, row["status"]),
            verification=row["verification"],
            trust=row["trust"],
            sensitivity=row["sensitivity"],
            source_refs=references,
            tags=tuple(strict_json_loads(row["tags_json"])),
            warnings=tuple(strict_json_loads(row["warnings_json"])),
            created_at=row["created_at"],
            activated_at=row["activated_at"],
            expires_at=row["expires_at"],
            supersedes_asset_id=row["supersedes_asset_id"],
            origin_uri=row["origin_uri"],
            content_sha256=row["content_sha256"],
        )

    def get_asset(self, asset_id: str, *, include_inactive: bool = False) -> KnowledgeAsset:
        if not isinstance(asset_id, str) or not _ASSET_ID.fullmatch(asset_id):
            raise ValueError("knowledge asset ID is invalid")
        query = "SELECT * FROM assets WHERE asset_id = ?"
        parameters: tuple[Any, ...] = (asset_id,)
        if not include_inactive:
            query += " AND status = 'active' AND (expires_at IS NULL OR expires_at > ?)"
            parameters = (asset_id, utc_now())
        row = self.connection.execute(query, parameters).fetchone()
        if row is None:
            raise KeyError(f"knowledge asset is unavailable: {asset_id}")
        return self._row_to_asset(row)

    def get_fragment(self, fragment_id: str) -> dict[str, Any]:
        if not isinstance(fragment_id, str) or not _FRAGMENT_ID.fullmatch(fragment_id):
            raise ValueError("knowledge fragment ID is invalid")
        row = self.connection.execute(
            """
            SELECT source_fragments.*, sources.title AS source_title,
                   sources.content_sha256 AS source_sha256,
                   sources.origin_uri AS source_origin_uri
            FROM source_fragments
            JOIN sources USING(source_id)
            WHERE fragment_id = ?
            """,
            (fragment_id,),
        ).fetchone()
        if row is None:
            raise KeyError(f"knowledge source fragment is unavailable: {fragment_id}")
        return {
            "fragment_id": row["fragment_id"],
            "source_id": row["source_id"],
            "source_title": row["source_title"],
            "source_origin_uri": row["source_origin_uri"],
            "source_sha256": row["source_sha256"],
            "ordinal": row["ordinal"],
            "locator": row["locator"],
            "text": row["text"],
            "text_sha256": row["text_sha256"],
            "instruction_risk": bool(row["instruction_risk"]),
        }

    def knowledge_lineage(
        self,
        *,
        knowledge_key: str | None = None,
        asset_id: str | None = None,
    ) -> dict[str, Any]:
        if not self.identity_v2_enabled:
            raise RuntimeError("Knowledge Identity v2 is not installed")
        if (knowledge_key is None) == (asset_id is None):
            raise ValueError("lineage lookup requires exactly one knowledge key or asset ID")
        if asset_id is not None:
            row = self.connection.execute(
                """
                SELECT knowledge_revisions_v2.knowledge_key
                FROM asset_revision_bindings_v2
                JOIN knowledge_revisions_v2 USING(asset_revision_id)
                WHERE legacy_asset_id = ?
                """,
                (asset_id,),
            ).fetchone()
            if row is None:
                raise KeyError(f"knowledge asset identity is unavailable: {asset_id}")
            knowledge_key = row["knowledge_key"]
        if not isinstance(knowledge_key, str) or not re.fullmatch(
            r"knowledge_[0-9a-f]{24}", knowledge_key
        ):
            raise ValueError("knowledge key is invalid")
        revision_rows = self.connection.execute(
            """
            SELECT knowledge_revisions_v2.*,
                   GROUP_CONCAT(asset_revision_bindings_v2.legacy_asset_id)
                       AS legacy_asset_ids
            FROM knowledge_revisions_v2
            LEFT JOIN asset_revision_bindings_v2 USING(asset_revision_id)
            WHERE knowledge_revisions_v2.knowledge_key = ?
            GROUP BY knowledge_revisions_v2.asset_revision_id
            ORDER BY knowledge_revisions_v2.asset_revision_id
            """,
            (knowledge_key,),
        ).fetchall()
        lineage_rows = self.connection.execute(
            """
            SELECT * FROM knowledge_lineage_v2
            WHERE knowledge_key = ? ORDER BY created_at, lineage_id
            """,
            (knowledge_key,),
        ).fetchall()
        return {
            "schema_version": "deeplaw.knowledge-lineage-view/v1",
            "vault_id": self.vault_id,
            "knowledge_key": knowledge_key,
            "revisions": [
                {
                    "asset_revision_id": row["asset_revision_id"],
                    "statement_sha256": row["statement_sha256"],
                    "source_revision_ids": strict_json_loads(row["source_revision_ids_json"]),
                    "logical_node_keys": strict_json_loads(row["logical_node_keys_json"]),
                    "legacy_asset_ids": (
                        row["legacy_asset_ids"].split(",") if row["legacy_asset_ids"] else []
                    ),
                }
                for row in revision_rows
            ],
            "transitions": [
                {
                    "lineage_id": row["lineage_id"],
                    "from_asset_revision_ids": strict_json_loads(
                        row["from_asset_revision_ids_json"]
                    ),
                    "to_asset_revision_ids": strict_json_loads(row["to_asset_revision_ids_json"]),
                    "status": row["status"],
                    "source_revision_id": row["source_revision_id"],
                    "mapping_evidence": strict_json_loads(row["mapping_evidence_json"]),
                    "created_at": row["created_at"],
                }
                for row in lineage_rows
            ],
        }

    def _latest_governance_revision(
        self,
        *,
        subject_kind: str,
        subject_id: str,
        as_of: str | None,
    ) -> sqlite3.Row | None:
        if as_of is None:
            parameters: tuple[Any, ...] = (subject_kind, subject_id)
            time_clause = ""
        else:
            canonical_timestamp(as_of, field="governance as_of")
            parameters = (subject_kind, subject_id, as_of)
            time_clause = "AND recorded_at <= ?"
        return self.connection.execute(
            f"""
            SELECT * FROM governance_revisions_v2
            WHERE subject_kind = ? AND subject_id = ? {time_clause}
            ORDER BY recorded_at DESC,
                     CASE
                         WHEN activation_status = 'inactive'
                          AND lifecycle_status NOT IN (
                              'pending', 'proposed', 'quarantined'
                          ) THEN 2
                         WHEN activation_status = 'active' THEN 1
                         ELSE 0
                     END DESC,
                     governance_revision DESC
            LIMIT 1
            """,
            parameters,
        ).fetchone()

    def _relation_evidence_admission_reasons(
        self,
        *,
        evidence_fragment_id: str,
        include_restricted: bool,
    ) -> list[str]:
        if not isinstance(include_restricted, bool):
            raise ValueError("relation evidence sensitivity flag must be a boolean")
        if self.identity_v2_enabled and self.control_enabled:
            source = self.connection.execute(
                """
                SELECT sources.sensitivity AS legacy_sensitivity,
                       source_lifecycle.status,
                       source_revision_bindings_v2.source_revision_id
                FROM source_fragments
                JOIN sources USING(source_id)
                LEFT JOIN source_lifecycle USING(source_id)
                JOIN source_revision_bindings_v2
                  ON source_revision_bindings_v2.legacy_source_id =
                     source_fragments.source_id
                WHERE source_fragments.fragment_id = ?
                """,
                (evidence_fragment_id,),
            ).fetchone()
        elif self.identity_v2_enabled:
            source = self.connection.execute(
                """
                SELECT sources.sensitivity AS legacy_sensitivity,
                       NULL AS status,
                       source_revision_bindings_v2.source_revision_id
                FROM source_fragments
                JOIN sources USING(source_id)
                JOIN source_revision_bindings_v2
                  ON source_revision_bindings_v2.legacy_source_id =
                     source_fragments.source_id
                WHERE source_fragments.fragment_id = ?
                """,
                (evidence_fragment_id,),
            ).fetchone()
        elif self.control_enabled:
            source = self.connection.execute(
                """
                SELECT sources.sensitivity AS legacy_sensitivity,
                       source_lifecycle.status, NULL AS source_revision_id
                FROM source_fragments
                JOIN sources USING(source_id)
                JOIN source_lifecycle USING(source_id)
                WHERE source_fragments.fragment_id = ?
                """,
                (evidence_fragment_id,),
            ).fetchone()
        else:
            source = self.connection.execute(
                """
                SELECT sources.sensitivity AS legacy_sensitivity,
                       NULL AS status, NULL AS source_revision_id
                FROM source_fragments JOIN sources USING(source_id)
                WHERE source_fragments.fragment_id = ?
                """,
                (evidence_fragment_id,),
            ).fetchone()
        if source is None:
            return ["relation_evidence_missing"]
        reasons: list[str] = []
        if self.control_enabled and source["status"] != "active":
            reasons.append("relation_evidence_source_inactive")
        sensitivity = source["legacy_sensitivity"]
        source_revision_id = source["source_revision_id"]
        if source_revision_id is not None:
            governance = self._latest_governance_revision(
                subject_kind="source_revision",
                subject_id=source_revision_id,
                as_of=None,
            )
            if governance is None:
                reasons.append("relation_evidence_governance_missing")
            else:
                sensitivity = governance["sensitivity"]
                if (
                    governance["review_status"] != "human_verified"
                    or governance["lifecycle_status"] != "active"
                    or governance["activation_status"] != "active"
                ):
                    reasons.append("relation_evidence_governance_inactive")
        if not include_restricted and sensitivity == "restricted":
            reasons.append("relation_evidence_restricted")
        return reasons

    def _relation_revision_admission_reasons(
        self,
        *,
        relation_revision_id: str,
        evidence_refs_json: str,
        as_of: str | None,
        include_restricted: bool,
    ) -> list[str]:
        """Recheck relation and evidence governance before current retrieval."""

        if as_of is not None:
            canonical_timestamp(as_of, field="relation evidence as_of")
        if not isinstance(include_restricted, bool):
            raise ValueError("relation evidence sensitivity flag must be a boolean")

        reasons: list[str] = []
        governance = self._latest_governance_revision(
            subject_kind="relation_revision",
            subject_id=relation_revision_id,
            as_of=as_of,
        )
        if governance is None:
            reasons.append("relation_governance_missing")
        else:
            if (
                governance["review_status"] != "human_verified"
                or governance["lifecycle_status"] != "active"
                or governance["activation_status"] != "active"
            ):
                reasons.append("relation_governance_inactive")
            if not include_restricted and governance["sensitivity"] == "restricted":
                reasons.append("relation_sensitivity:restricted")

        try:
            evidence_refs = strict_json_loads(evidence_refs_json)
        except (TypeError, UnicodeDecodeError, ValueError):
            return [*reasons, "relation_evidence_invalid"]
        if not isinstance(evidence_refs, list) or not evidence_refs:
            return [*reasons, "relation_evidence_missing"]
        for reference in evidence_refs:
            if not isinstance(reference, dict):
                reasons.append("relation_evidence_invalid")
                continue
            source_revision_id = reference.get("source_revision_id")
            if not isinstance(source_revision_id, str):
                reasons.append("relation_evidence_invalid")
                continue
            source = self.connection.execute(
                """
                SELECT source_lifecycle.status, source_lifecycle.activated_at,
                       source_lifecycle.superseded_at, source_lifecycle.removed_at
                FROM source_revision_bindings_v2
                JOIN source_lifecycle
                  ON source_lifecycle.source_id =
                     source_revision_bindings_v2.legacy_source_id
                WHERE source_revision_bindings_v2.source_revision_id = ?
                """,
                (source_revision_id,),
            ).fetchone()
            if source is None:
                reasons.append(f"relation_source_missing:{source_revision_id}")
                continue
            if as_of is None:
                source_active = source["status"] == "active"
            else:
                source_active = bool(
                    source["activated_at"] is not None
                    and source["activated_at"] <= as_of
                    and (
                        source["superseded_at"] is None
                        or source["superseded_at"] > as_of
                    )
                    and (
                        source["removed_at"] is None
                        or source["removed_at"] > as_of
                    )
                )
            if not source_active:
                reasons.append(f"relation_source_inactive:{source_revision_id}")
            source_governance = self._latest_governance_revision(
                subject_kind="source_revision",
                subject_id=source_revision_id,
                as_of=as_of,
            )
            if source_governance is None:
                reasons.append(f"relation_source_governance_missing:{source_revision_id}")
                continue
            if source_governance["review_status"] != "human_verified":
                reasons.append(f"relation_source_unreviewed:{source_revision_id}")
            if (
                not include_restricted
                and source_governance["sensitivity"] == "restricted"
            ):
                reasons.append(f"relation_source_restricted:{source_revision_id}")
        return list(dict.fromkeys(reasons))

    def temporal_relations(
        self,
        *,
        mode: Literal["current", "past", "as-of"] = "current",
        as_of: str | None = None,
        limit: int = 100,
    ) -> dict[str, Any]:
        if not self.identity_v2_enabled:
            raise RuntimeError("Knowledge Identity v2 is not installed")
        if mode not in {"current", "past", "as-of"}:
            raise ValueError("temporal relation mode is invalid")
        if isinstance(limit, bool) or not 1 <= limit <= 500:
            raise ValueError("temporal relation limit must be between 1 and 500")
        selected_time = as_of or utc_now()
        canonical_timestamp(selected_time, field="temporal relation as_of")
        if mode == "as-of" and as_of is None:
            raise ValueError("as-of relation lookup requires an exact timestamp")
        if mode == "current":
            rows = self.connection.execute(
                """
                WITH eligible AS (
                    SELECT relation_revisions_v2.*,
                           ROW_NUMBER() OVER (
                               PARTITION BY relation_key
                               ORDER BY observed_at DESC, relation_revision_id DESC
                           ) AS relation_rank
                    FROM relation_revisions_v2
                )
                SELECT eligible.* FROM eligible
                JOIN asset_revision_bindings_v2 AS subject_binding
                  ON subject_binding.asset_revision_id =
                     eligible.subject_asset_revision_id
                JOIN assets AS subject_asset
                  ON subject_asset.asset_id = subject_binding.legacy_asset_id
                JOIN asset_revision_bindings_v2 AS object_binding
                  ON object_binding.asset_revision_id =
                     eligible.object_asset_revision_id
                JOIN assets AS object_asset
                  ON object_asset.asset_id = object_binding.legacy_asset_id
                WHERE relation_rank = 1 AND eligible.status = 'active'
                  AND subject_asset.status = 'active'
                  AND object_asset.status = 'active'
                  AND (eligible.valid_from IS NULL OR eligible.valid_from <= ?)
                  AND (eligible.valid_to IS NULL OR eligible.valid_to > ?)
                ORDER BY eligible.observed_at DESC, eligible.relation_revision_id
                LIMIT ?
                """,
                (selected_time, selected_time, limit),
            ).fetchall()
        elif mode == "as-of":
            rows = self.connection.execute(
                """
                WITH eligible AS (
                    SELECT relation_revisions_v2.*,
                           ROW_NUMBER() OVER (
                               PARTITION BY relation_key
                               ORDER BY observed_at DESC, relation_revision_id DESC
                           ) AS relation_rank
                    FROM relation_revisions_v2
                    WHERE observed_at <= ?
                ), subject_governance AS (
                    SELECT governance_revisions_v2.*,
                           ROW_NUMBER() OVER (
                               PARTITION BY subject_id
                               ORDER BY recorded_at DESC,
                                        CASE
                                            WHEN activation_status = 'inactive'
                                             AND lifecycle_status NOT IN (
                                                 'pending', 'proposed', 'quarantined'
                                             ) THEN 2
                                            WHEN activation_status = 'active' THEN 1
                                            ELSE 0
                                        END DESC,
                                        governance_revision DESC
                           ) AS governance_rank
                    FROM governance_revisions_v2
                    WHERE subject_kind = 'asset_revision' AND recorded_at <= ?
                ), object_governance AS (
                    SELECT governance_revisions_v2.*,
                           ROW_NUMBER() OVER (
                               PARTITION BY subject_id
                               ORDER BY recorded_at DESC,
                                        CASE
                                            WHEN activation_status = 'inactive'
                                             AND lifecycle_status NOT IN (
                                                 'pending', 'proposed', 'quarantined'
                                             ) THEN 2
                                            WHEN activation_status = 'active' THEN 1
                                            ELSE 0
                                        END DESC,
                                        governance_revision DESC
                           ) AS governance_rank
                    FROM governance_revisions_v2
                    WHERE subject_kind = 'asset_revision' AND recorded_at <= ?
                )
                SELECT eligible.* FROM eligible
                JOIN subject_governance
                  ON subject_governance.subject_id = eligible.subject_asset_revision_id
                 AND subject_governance.governance_rank = 1
                JOIN object_governance
                  ON object_governance.subject_id = eligible.object_asset_revision_id
                 AND object_governance.governance_rank = 1
                WHERE relation_rank = 1 AND eligible.status = 'active'
                  AND subject_governance.review_status = 'human_verified'
                  AND subject_governance.lifecycle_status = 'active'
                  AND subject_governance.activation_status = 'active'
                  AND object_governance.review_status = 'human_verified'
                  AND object_governance.lifecycle_status = 'active'
                  AND object_governance.activation_status = 'active'
                  AND (eligible.valid_from IS NULL OR eligible.valid_from <= ?)
                  AND (eligible.valid_to IS NULL OR eligible.valid_to > ?)
                ORDER BY eligible.observed_at DESC, eligible.relation_revision_id
                LIMIT ?
                """,
                (
                    selected_time,
                    selected_time,
                    selected_time,
                    selected_time,
                    selected_time,
                    limit,
                ),
            ).fetchall()
        else:
            rows = self.connection.execute(
                """
                WITH eligible AS (
                    SELECT relation_revisions_v2.*,
                           ROW_NUMBER() OVER (
                               PARTITION BY relation_key
                               ORDER BY observed_at DESC, relation_revision_id DESC
                           ) AS relation_rank
                    FROM relation_revisions_v2
                )
                SELECT eligible.* FROM eligible
                JOIN asset_revision_bindings_v2 AS subject_binding
                  ON subject_binding.asset_revision_id =
                     eligible.subject_asset_revision_id
                JOIN assets AS subject_asset
                  ON subject_asset.asset_id = subject_binding.legacy_asset_id
                JOIN asset_revision_bindings_v2 AS object_binding
                  ON object_binding.asset_revision_id =
                     eligible.object_asset_revision_id
                JOIN assets AS object_asset
                  ON object_asset.asset_id = object_binding.legacy_asset_id
                WHERE relation_rank > 1 OR eligible.status <> 'active'
                   OR (eligible.valid_to IS NOT NULL AND eligible.valid_to <= ?)
                   OR subject_asset.status <> 'active'
                   OR object_asset.status <> 'active'
                ORDER BY eligible.observed_at DESC, eligible.relation_revision_id
                LIMIT ?
                """,
                (selected_time, limit),
            ).fetchall()
        if mode in {"current", "as-of"}:
            selected_as_of = selected_time if mode == "as-of" else None
            rows = [
                row
                for row in rows
                if not self._relation_revision_admission_reasons(
                    relation_revision_id=row["relation_revision_id"],
                    evidence_refs_json=row["evidence_refs_json"],
                    as_of=selected_as_of,
                    include_restricted=True,
                )
            ]
        return {
            "schema_version": "deeplaw.temporal-relation-view/v1",
            "vault_id": self.vault_id,
            "mode": mode,
            "as_of": selected_time,
            "relations": [
                {
                    "relation_key": row["relation_key"],
                    "relation_revision_id": row["relation_revision_id"],
                    "subject_knowledge_key": row["subject_knowledge_key"],
                    "object_knowledge_key": row["object_knowledge_key"],
                    "predicate": row["predicate"],
                    "evidence_refs": strict_json_loads(row["evidence_refs_json"]),
                    "status": row["status"],
                    "event_time": row["event_time"],
                    "valid_from": row["valid_from"],
                    "valid_to": row["valid_to"],
                    "observed_at": row["observed_at"],
                    "reviewed_at": row["reviewed_at"],
                    "ingest_time": row["ingest_time"],
                }
                for row in rows
            ],
            "truncated": len(rows) == limit,
            "applicability_notice": (
                "Temporal matching does not establish factual or legal applicability."
            ),
        }

    def source_file_path(self, source_id: str) -> Path:
        if not isinstance(source_id, str) or not _SOURCE_ID.fullmatch(source_id):
            raise ValueError("knowledge source ID is invalid")
        row = self.connection.execute(
            "SELECT stored_name FROM sources WHERE source_id = ?",
            (source_id,),
        ).fetchone()
        if row is None or not isinstance(row["stored_name"], str):
            raise KeyError(f"knowledge source file is unavailable: {source_id}")
        stored_name = row["stored_name"]
        if not stored_name or Path(stored_name).name != stored_name or len(stored_name) > 100:
            raise RuntimeError("knowledge source stored name is unsafe")
        return self.root / "sources" / stored_name

    def _source_file_check(
        self,
        source_id: str,
        *,
        cache: dict[str, dict[str, Any]] | None = None,
    ) -> dict[str, Any]:
        if cache is not None and source_id in cache:
            return cache[source_id]
        source = self.connection.execute(
            """
            SELECT stored_name, byte_size, content_sha256
            FROM sources WHERE source_id = ?
            """,
            (source_id,),
        ).fetchone()
        source_valid = False
        reason: str | None = "source_record_missing"
        if source is not None and source["stored_name"]:
            try:
                source_path = self.source_file_path(source_id)
                if (
                    not source_path.is_symlink()
                    and source_path.is_file()
                    and source_path.stat().st_size == source["byte_size"]
                    and self._cached_source_sha256(source_path) == source["content_sha256"]
                ):
                    source_valid = True
                    reason = None
                else:
                    reason = "source_file_missing_or_hash_mismatch"
            except (KeyError, OSError, RuntimeError, ValueError):
                reason = "source_file_missing_or_hash_mismatch"
        result = {
            "source_id": source_id,
            "content_sha256": (source["content_sha256"] if source is not None else None),
            "valid": source_valid,
            "reason": reason,
        }
        if cache is not None:
            cache[source_id] = result
        return result

    @staticmethod
    def _cached_source_sha256(path: Path) -> str:
        stat_result = path.stat()
        fingerprint = (
            str(path),
            stat_result.st_dev,
            stat_result.st_ino,
            stat_result.st_size,
            stat_result.st_mtime_ns,
            stat_result.st_ctime_ns,
        )
        with _SOURCE_HASH_CACHE_LOCK:
            cached = _SOURCE_HASH_CACHE.get(fingerprint)
            if cached is not None:
                _SOURCE_HASH_CACHE.move_to_end(fingerprint)
                return cached
        digest = sha256_file(path)
        current = path.stat()
        current_fingerprint = (
            str(path),
            current.st_dev,
            current.st_ino,
            current.st_size,
            current.st_mtime_ns,
            current.st_ctime_ns,
        )
        if current_fingerprint != fingerprint:
            raise RuntimeError("knowledge source file changed during integrity verification")
        with _SOURCE_HASH_CACHE_LOCK:
            for existing_key in tuple(_SOURCE_HASH_CACHE):
                if existing_key[0] == str(path) and existing_key != fingerprint:
                    del _SOURCE_HASH_CACHE[existing_key]
            _SOURCE_HASH_CACHE[fingerprint] = digest
            _SOURCE_HASH_CACHE.move_to_end(fingerprint)
            while len(_SOURCE_HASH_CACHE) > _MAX_SOURCE_HASH_CACHE_ENTRIES:
                _SOURCE_HASH_CACHE.popitem(last=False)
        return digest

    def verify_source_files(
        self,
        source_ids: Iterable[str],
    ) -> dict[str, Any]:
        identifiers = tuple(dict.fromkeys(source_ids))
        if len(identifiers) > _MAX_FRAGMENTS_PER_SOURCE:
            raise ValueError("source-file verification exceeds its record bound")
        cache: dict[str, dict[str, Any]] = {}
        checks = [self._source_file_check(source_id, cache=cache) for source_id in identifiers]
        return {
            "valid": all(check["valid"] for check in checks),
            "checked_source_files": len(checks),
            "checks": checks,
        }

    def search(
        self,
        query: str,
        *,
        limit: int = 5,
        max_chars: int = 5_000,
        kinds: Iterable[str] = (),
        memory_tiers: Iterable[str] = (),
        include_restricted: bool = False,
        include_inactive: bool = False,
    ) -> KnowledgeSearchResponse:
        self._require_healthy_integrity()
        query = query.strip()
        if not query or len(query) > 4_000:
            raise ValueError("knowledge query must be between 1 and 4000 characters")
        if isinstance(limit, bool) or not 1 <= limit <= _MAX_SEARCH_LIMIT:
            raise ValueError(f"knowledge search limit must be between 1 and {_MAX_SEARCH_LIMIT}")
        if isinstance(max_chars, bool) or not 1 <= max_chars <= _MAX_SEARCH_CHARS:
            raise ValueError(f"knowledge max_chars must be between 1 and {_MAX_SEARCH_CHARS}")
        selected_kinds = tuple(dict.fromkeys(kinds))
        selected_tiers = tuple(dict.fromkeys(memory_tiers))
        if any(kind not in ASSET_KINDS for kind in selected_kinds):
            raise ValueError("knowledge search contains an unsupported asset kind")
        if any(tier not in MEMORY_TIERS for tier in selected_tiers):
            raise ValueError("knowledge search contains an unsupported memory tier")
        terms = search_terms(query, limit=32, cover_tail=True)
        if not terms:
            raise ValueError("knowledge query has no searchable terms")
        conditions = ["asset_search MATCH ?"]
        parameters: list[Any] = [fts_query(terms)]
        if not include_inactive:
            conditions.append("assets.status = 'active'")
            conditions.append("(assets.expires_at IS NULL OR assets.expires_at > ?)")
            parameters.append(utc_now())
        if not include_restricted:
            conditions.append("assets.sensitivity <> 'restricted'")
        if selected_kinds:
            conditions.append(f"assets.kind IN ({','.join('?' for _ in selected_kinds)})")
            parameters.extend(selected_kinds)
        if selected_tiers:
            conditions.append(f"assets.memory_tier IN ({','.join('?' for _ in selected_tiers)})")
            parameters.extend(selected_tiers)
        parameters.append(64)
        # Every SQL fragment above is a closed literal; caller values remain
        # bound parameters and only placeholder counts are interpolated.
        rows = self.connection.execute(
            f"""
            SELECT assets.*, bm25(asset_search, 0.0, 8.0, 3.0, 10.0, 2.0) AS rank
            FROM asset_search
            JOIN assets USING(asset_id)
            WHERE {" AND ".join(conditions)}
            ORDER BY rank, assets.asset_id
            LIMIT ?
            """,
            tuple(parameters),
        ).fetchall()
        compact_query = compact_text(query)
        scored: list[tuple[float, KnowledgeAsset, str]] = []
        for row in rows:
            asset = self._row_to_asset(row)
            score = -float(row["rank"])
            reasons = ["lexical"]
            if asset.semantic_key and compact_text(asset.semantic_key) == compact_query:
                score += 100.0
                reasons.append("semantic_key_exact")
            if compact_query and compact_query in compact_text(asset.title):
                score += 40.0
                reasons.append("title_exact")
            if asset.kind in {"constraint", "decision", "rule", "procedure"}:
                score += 4.0
            if asset.verification == "human_verified":
                score += 3.0
            scored.append((score, asset, "+".join(reasons)))
        scored.sort(key=lambda item: (-item[0], item[1].asset_id))
        cards: list[KnowledgeCard] = []
        total_chars = 0
        source_file_cache: dict[str, dict[str, Any]] = {}
        excluded_by_source_integrity = 0
        for score, asset, hit_reason in scored:
            remaining = max_chars - total_chars
            if remaining <= 0 or len(cards) >= limit:
                break
            if any(
                not self._source_file_check(
                    reference.source_id,
                    cache=source_file_cache,
                )["valid"]
                for reference in asset.source_refs
            ):
                excluded_by_source_integrity += 1
                continue
            card_excerpt = excerpt(
                asset.statement,
                query,
                max_chars=min(700, remaining),
                cover_query_tail=True,
            )
            if not card_excerpt:
                continue
            cards.append(
                KnowledgeCard(
                    asset_id=asset.asset_id,
                    uri=asset.uri,
                    kind=asset.kind,
                    memory_tier=asset.memory_tier,
                    title=asset.title,
                    excerpt=card_excerpt,
                    semantic_key=asset.semantic_key,
                    verification=asset.verification,
                    trust=asset.trust,
                    sensitivity=asset.sensitivity,
                    directive_mode=asset.directive_mode,
                    source_refs=asset.source_refs,
                    tags=asset.tags,
                    content_sha256=asset.content_sha256,
                    score=round(score, 6),
                    hit_reason=hit_reason,
                )
            )
            total_chars += len(card_excerpt)
        gaps: list[str] = []
        if not cards:
            gaps.append("no active reviewed knowledge asset matched the task")
        if excluded_by_source_integrity:
            gaps.append(
                f"{excluded_by_source_integrity} matched asset(s) were excluded because "
                "their stored source file failed integrity verification"
            )
        return KnowledgeSearchResponse(
            vault_id=self.vault_id,
            vault_revision=self.revision,
            query=query,
            results=tuple(cards),
            gaps=tuple(gaps),
            total_excerpt_chars=total_chars,
        )

    def relations_for_assets(
        self,
        asset_ids: Iterable[str],
        *,
        limit: int = 32,
        include_restricted: bool = False,
        require_evidence: bool = False,
    ) -> list[dict[str, Any]]:
        identifiers = tuple(dict.fromkeys(asset_ids))
        if not identifiers:
            return []
        if len(identifiers) > 20 or not 1 <= limit <= 64:
            raise ValueError("knowledge relation expansion exceeds its bound")
        if not isinstance(include_restricted, bool):
            raise ValueError("knowledge relation sensitivity flag must be a boolean")
        if not isinstance(require_evidence, bool):
            raise ValueError("knowledge relation evidence flag must be a boolean")
        if require_evidence and not self.control_enabled:
            return []
        candidate_limit = min(256, max(limit, limit * 4))
        placeholders = ",".join("?" for _ in identifiers)
        # identifiers determine only the number of bound placeholders.
        rows = self.connection.execute(
            f"""
            WITH candidate_relations AS MATERIALIZED (
                SELECT relation_id
                FROM relations
                WHERE subject_asset_id IN ({placeholders})
                UNION
                SELECT relation_id
                FROM relations
                WHERE object_asset_id IN ({placeholders})
            )
            SELECT relations.*
            FROM candidate_relations
            CROSS JOIN relations
              ON relations.relation_id = candidate_relations.relation_id
            CROSS JOIN assets AS subject
              ON subject.asset_id = relations.subject_asset_id
            CROSS JOIN assets AS object
              ON object.asset_id = relations.object_asset_id
            WHERE relations.verification = 'human_verified'
              AND subject.status = 'active'
              AND object.status = 'active'
              AND (subject.expires_at IS NULL OR subject.expires_at > ?)
              AND (object.expires_at IS NULL OR object.expires_at > ?)
              AND (? OR (subject.sensitivity <> 'restricted'
                         AND object.sensitivity <> 'restricted'))
            ORDER BY relations.relation_id
            LIMIT ?
            """,
            (
                *identifiers,
                *identifiers,
                utc_now(),
                utc_now(),
                int(include_restricted),
                candidate_limit,
            ),
        ).fetchall()
        admitted: list[dict[str, Any]] = []
        for row in rows:
            evidence_fragment_id = row["evidence_fragment_id"]
            if evidence_fragment_id is None:
                if require_evidence:
                    continue
            elif self._relation_evidence_admission_reasons(
                evidence_fragment_id=evidence_fragment_id,
                include_restricted=include_restricted,
            ):
                continue
            admitted.append(dict(row))
            if len(admitted) >= limit:
                break
        return admitted

    def verify_asset(self, asset_id: str) -> dict[str, Any]:
        asset = self.get_asset(asset_id, include_inactive=True)
        reference_checks: list[dict[str, Any]] = []
        source_file_checks: list[dict[str, Any]] = []
        source_file_cache: dict[str, dict[str, Any]] = {}
        valid = True
        for reference in asset.source_refs:
            try:
                fragment = self.get_fragment(reference.fragment_id)
            except KeyError:
                valid = False
                reference_checks.append(
                    {
                        "fragment_id": reference.fragment_id,
                        "valid": False,
                        "reason": "fragment_missing",
                    }
                )
                continue
            reference_valid = (
                fragment["source_id"] == reference.source_id
                and fragment["locator"] == reference.locator
                and fragment["text_sha256"] == reference.quote_sha256
                and sha256_bytes(fragment["text"].encode("utf-8")) == reference.quote_sha256
            )
            valid = valid and reference_valid
            reference_checks.append(
                {
                    "fragment_id": reference.fragment_id,
                    "valid": reference_valid,
                    "reason": None if reference_valid else "source_binding_mismatch",
                }
            )
            if reference.source_id in source_file_cache:
                continue
            source_check = self._source_file_check(
                reference.source_id,
                cache=source_file_cache,
            )
            valid = valid and bool(source_check["valid"])
            source_file_checks.append(source_check)
        integrity = self.verify_integrity()
        integrity_valid = valid and bool(integrity["valid"])
        agent_usable = (
            asset.status == "active"
            and asset.verification == "human_verified"
            and (asset.expires_at is None or asset.expires_at > utc_now())
            and integrity_valid
        )
        return {
            "schema_version": "deeplaw.knowledge-verification/v1",
            "vault_id": self.vault_id,
            "vault_revision": self.revision,
            "asset_id": asset.asset_id,
            "uri": asset.uri,
            "content_sha256": asset.content_sha256,
            "status": asset.status,
            "verification": asset.verification,
            "source_references": reference_checks,
            "source_files": source_file_checks,
            "audit_head": self.audit_head,
            "audit_chain_valid": integrity["audit"]["valid"],
            "state_integrity_valid": integrity["state"]["valid"],
            "integrity_valid": integrity_valid,
            "agent_usable": agent_usable,
            "valid": integrity_valid and agent_usable,
        }

    def verify_audit_chain(self) -> dict[str, Any]:
        event_count = self.connection.execute("SELECT COUNT(*) FROM events").fetchone()[0]
        previous_hash: str | None = None
        expected_sequence = 0
        for row in self.connection.execute("SELECT * FROM events ORDER BY sequence"):
            if row["sequence"] != expected_sequence or row["previous_hash"] != previous_hash:
                return {
                    "valid": False,
                    "event_count": event_count,
                    "failed_sequence": expected_sequence,
                    "reason": "sequence_or_previous_hash_mismatch",
                }
            try:
                payload = strict_json_loads(row["payload_json"])
            except (json.JSONDecodeError, ValueError):
                return {
                    "valid": False,
                    "event_count": event_count,
                    "failed_sequence": expected_sequence,
                    "reason": "event_payload_invalid",
                }
            event = {
                "schema_version": row["schema_version"],
                "sequence": row["sequence"],
                "event_type": row["event_type"],
                "object_id": row["object_id"],
                "payload": payload,
                "previous_hash": row["previous_hash"],
                "created_at": row["created_at"],
            }
            event_hash = sha256_bytes(canonical_json(event).encode("utf-8"))
            if event_hash != row["event_hash"]:
                return {
                    "valid": False,
                    "event_count": event_count,
                    "failed_sequence": expected_sequence,
                    "reason": "event_hash_mismatch",
                }
            previous_hash = event_hash
            expected_sequence += 1
        valid = (
            event_count > 0
            and previous_hash == self.audit_head
            and event_count == self.revision + 1
        )
        return {
            "valid": valid,
            "event_count": event_count,
            "failed_sequence": None if valid else expected_sequence,
            "reason": None if valid else "audit_head_or_revision_mismatch",
        }

    def verify_state_integrity(self) -> dict[str, Any]:
        counts = {
            "asset_count": self.connection.execute("SELECT COUNT(*) FROM assets").fetchone()[0],
            "source_count": self.connection.execute("SELECT COUNT(*) FROM sources").fetchone()[0],
            "fragment_count": self.connection.execute(
                "SELECT COUNT(*) FROM source_fragments"
            ).fetchone()[0],
            "relation_count": self.connection.execute("SELECT COUNT(*) FROM relations").fetchone()[
                0
            ],
            "search_index_count": self.connection.execute(
                "SELECT COUNT(*) FROM asset_search"
            ).fetchone()[0],
            "source_lifecycle_count": (
                self.connection.execute("SELECT COUNT(*) FROM source_lifecycle").fetchone()[0]
                if self.control_enabled
                else 0
            ),
            "review_receipt_count": (
                self.connection.execute("SELECT COUNT(*) FROM review_receipts").fetchone()[0]
                if self.control_enabled
                else 0
            ),
            "run_receipt_count": (
                self.connection.execute("SELECT COUNT(*) FROM run_receipts").fetchone()[0]
                if self.control_enabled
                else 0
            ),
            "feedback_count": (
                self.connection.execute("SELECT COUNT(*) FROM feedback_records").fetchone()[0]
                if self.control_enabled
                else 0
            ),
        }

        def failed(reason: str, object_id: str | None = None) -> dict[str, Any]:
            return {
                "valid": False,
                **counts,
                "reason": reason,
                "object_id": object_id,
            }

        try:
            assets: dict[str, KnowledgeAsset] = {}
            for row in self.connection.execute("SELECT * FROM assets ORDER BY asset_id"):
                asset_id = row["asset_id"]
                if asset_id in assets:
                    return failed("duplicate_asset_identity")
                assets[asset_id] = self._row_to_asset(row)
            sources: dict[str, dict[str, Any]] = {}
            source_instruction_risk: dict[str, int] = {}
            for row in self.connection.execute("SELECT * FROM sources ORDER BY source_id"):
                source_id = row["source_id"]
                if source_id in sources:
                    return failed("duplicate_source_identity")
                sources[source_id] = self._source_row(row)
                source_instruction_risk[source_id] = row["instruction_risk"]
            fragments: dict[str, dict[str, Any]] = {}
            for row in self.connection.execute(
                "SELECT * FROM source_fragments ORDER BY fragment_id"
            ):
                fragment_id = row["fragment_id"]
                if fragment_id in fragments:
                    return failed("duplicate_fragment_identity")
                fragments[fragment_id] = dict(row)
            relations: dict[str, dict[str, Any]] = {}
            for row in self.connection.execute("SELECT * FROM relations ORDER BY relation_id"):
                relation_id = row["relation_id"]
                if relation_id in relations:
                    return failed("duplicate_relation_identity")
                relations[relation_id] = dict(row)
            source_lifecycles = (
                {
                    row["source_id"]: dict(row)
                    for row in self.connection.execute(
                        "SELECT * FROM source_lifecycle ORDER BY source_id"
                    )
                }
                if self.control_enabled
                else {}
            )
            review_receipts = (
                {
                    row["review_receipt_id"]: dict(row)
                    for row in self.connection.execute(
                        "SELECT * FROM review_receipts ORDER BY review_receipt_id"
                    )
                }
                if self.control_enabled
                else {}
            )
            run_receipts = (
                {
                    row["run_id"]: dict(row)
                    for row in self.connection.execute("SELECT * FROM run_receipts ORDER BY run_id")
                }
                if self.control_enabled
                else {}
            )
            feedback_records = (
                {
                    row["feedback_id"]: dict(row)
                    for row in self.connection.execute(
                        "SELECT * FROM feedback_records ORDER BY feedback_id"
                    )
                }
                if self.control_enabled
                else {}
            )
            identity_fragments: dict[str, dict[str, Any]] = {}
            if self.identity_v2_enabled:
                for row in self.connection.execute(
                    """
                    SELECT legacy_fragment_bindings_v2.fragment_id,
                           fragments_v2.fragment_revision_id,
                           fragments_v2.compilation_id,
                           fragments_v2.ordinal,
                           fragments_v2.locator,
                           fragments_v2.text_sha256,
                           fragments_v2.instruction_risk,
                           fragment_node_membership_v2.node_ordinal,
                           source_ir_nodes_v2.logical_node_key,
                           source_ir_nodes_v2.title
                    FROM legacy_fragment_bindings_v2
                    JOIN fragments_v2 USING(fragment_revision_id)
                    LEFT JOIN fragment_node_membership_v2 USING(fragment_revision_id)
                    LEFT JOIN source_ir_nodes_v2 USING(node_id)
                    ORDER BY legacy_fragment_bindings_v2.fragment_id,
                             fragment_node_membership_v2.node_ordinal
                    """
                ):
                    entry = identity_fragments.setdefault(
                        row["fragment_id"],
                        {
                            "fragment_revision_id": row["fragment_revision_id"],
                            "compilation_id": row["compilation_id"],
                            "ordinal": row["ordinal"],
                            "locator": row["locator"],
                            "text_sha256": row["text_sha256"],
                            "instruction_risk": row["instruction_risk"],
                            "logical_node_keys": [],
                            "title": row["title"],
                        },
                    )
                    if row["logical_node_key"] is not None:
                        entry["logical_node_keys"].append(row["logical_node_key"])
        except (KeyError, TypeError, ValueError):
            return failed("stored_record_contract_invalid")
        if len(assets) != counts["asset_count"]:
            return failed("duplicate_asset_identity")
        if len(sources) != counts["source_count"]:
            return failed("duplicate_source_identity")
        if len(fragments) != counts["fragment_count"]:
            return failed("duplicate_fragment_identity")
        if len(relations) != counts["relation_count"]:
            return failed("duplicate_relation_identity")

        fragment_ids_by_source: dict[str, list[tuple[int, str]]] = {}
        for fragment_id, fragment in fragments.items():
            fragment_ids_by_source.setdefault(fragment["source_id"], []).append(
                (fragment["ordinal"], fragment_id)
            )
        asset_id_by_fragment: dict[str, str] = {}
        duplicate_asset_fragments: set[str] = set()
        for asset_id, asset in assets.items():
            if len(asset.source_refs) != 1:
                continue
            fragment_id = asset.source_refs[0].fragment_id
            if fragment_id in asset_id_by_fragment:
                duplicate_asset_fragments.add(fragment_id)
                continue
            asset_id_by_fragment[fragment_id] = asset_id

        expected_assets: dict[str, dict[str, Any]] = {}
        expected_sources: set[str] = set()
        expected_fragments: set[str] = set()
        expected_relations: dict[str, dict[str, Any]] = {}
        expected_source_lifecycles: dict[str, dict[str, Any]] = {}
        expected_review_receipts: set[str] = set()
        expected_run_receipts: set[str] = set()
        expected_feedback_records: set[str] = set()
        latest_identity_root: str | None = None
        previous_event_at: str | None = None
        for event in self.connection.execute("SELECT * FROM events ORDER BY sequence"):
            event_type = event["event_type"]
            object_id = event["object_id"]
            if (
                event["schema_version"] != KNOWLEDGE_EVENT_SCHEMA
                or event_type not in _KNOWN_EVENT_TYPES
            ):
                return failed("unknown_or_invalid_event_type", object_id)
            try:
                payload = strict_json_loads(event["payload_json"])
                event_at = canonical_timestamp(
                    event["created_at"],
                    field="knowledge event created_at",
                )
            except (json.JSONDecodeError, ValueError):
                return failed("event_payload_invalid", object_id)
            if previous_event_at is not None and event_at < previous_event_at:
                return failed("event_timestamp_order_invalid", object_id)
            previous_event_at = event_at
            if not isinstance(payload, dict):
                return failed("event_payload_not_object", object_id)

            if event_type == "vault_initialized":
                if (
                    event["sequence"] != 0
                    or object_id != self.vault_id
                    or payload
                    != {
                        "name": self.manifest["name"],
                        "scope": self.manifest["scope"],
                    }
                ):
                    return failed("vault_initialization_event_mismatch", object_id)
                continue

            if event_type == "source_compiled":
                legacy_payload = {
                    "source_sha256",
                    "fragment_ids",
                    "asset_ids",
                    "instruction_risk",
                    "compiler",
                }
                compact_payload = {
                    "source_sha256",
                    "fragment_count",
                    "asset_count",
                    "membership_sha256",
                    "instruction_risk",
                    "compiler",
                }
                control_payload = {
                    *compact_payload,
                    "source_key",
                    "previous_source_id",
                    "source_status",
                }
                identity_v2_payload = {
                    "source_sha256",
                    "fragment_count",
                    "fragment_inventory_sha256",
                    "proposal_count",
                    "proposal_inventory_sha256",
                    "proposal_ref_graph_sha256",
                    "asset_count",
                    "membership_sha256",
                    "instruction_risk",
                    "compiler",
                    "source_key",
                    "previous_source_id",
                    "source_status",
                    "source_revision_id",
                    "compilation_id",
                    "proposal_set_id",
                }
                payload_fields = frozenset(payload)
                if (
                    payload_fields
                    not in {
                        frozenset(legacy_payload),
                        frozenset(compact_payload),
                        frozenset(control_payload),
                        frozenset(identity_v2_payload),
                    }
                    or not isinstance(object_id, str)
                    or object_id not in sources
                    or object_id in expected_sources
                    or not isinstance(payload["instruction_risk"], bool)
                    or not isinstance(payload["compiler"], dict)
                ):
                    return failed("source_compiled_event_invalid", object_id)
                source = sources[object_id]
                if payload_fields in {
                    frozenset(control_payload),
                    frozenset(identity_v2_payload),
                }:
                    source_key = payload["source_key"]
                    previous_source_id = payload["previous_source_id"]
                    if (
                        not isinstance(source_key, str)
                        or not _SOURCE_KEY.fullmatch(source_key)
                        or payload["source_status"] != "pending"
                        or (
                            previous_source_id is not None
                            and (
                                not isinstance(previous_source_id, str)
                                or previous_source_id not in sources
                            )
                        )
                    ):
                        return failed("source_lifecycle_event_invalid", object_id)
                    expected_source_id = stable_id(
                        "source",
                        self.vault_id,
                        source["kind"],
                        source["content_sha256"],
                        source["title"],
                        source["origin_uri"] or "",
                        source["trust"],
                        source["sensitivity"],
                        canonical_json(source["compiler"]),
                    )
                    expected_source_lifecycles[object_id] = {
                        "source_key": source_key,
                        "previous_source_id": previous_source_id,
                        "status": "pending",
                        "activated_at": None,
                        "superseded_at": None,
                        "removed_at": None,
                    }
                else:
                    expected_source_id = stable_id(
                        "source",
                        self.vault_id,
                        source["kind"],
                        source["content_sha256"],
                        source["title"],
                        source["origin_uri"] or "",
                        source["trust"],
                        source["sensitivity"],
                        canonical_json(source["compiler"]),
                    )
                if (
                    expected_source_id != object_id
                    or source["content_sha256"] != payload["source_sha256"]
                    or source["compiler"] != payload["compiler"]
                    or source["instruction_risk"] != payload["instruction_risk"]
                    or source["kind"] not in SOURCE_KINDS
                    or source["trust"] not in TRUST_LEVELS
                    or source["sensitivity"] not in SENSITIVITY_LEVELS
                    or not isinstance(source["title"], str)
                    or source["title"] != source["title"].strip()
                    or not 1 <= len(source["title"]) <= 500
                    or (
                        source["origin_uri"] is not None
                        and (
                            not isinstance(source["origin_uri"], str)
                            or source["origin_uri"] != source["origin_uri"].strip()
                            or not 1 <= len(source["origin_uri"]) <= 2_000
                        )
                    )
                    or not isinstance(source["media_type"], str)
                    or source["media_type"] != source["media_type"].strip()
                    or not 1 <= len(source["media_type"]) <= 200
                    or isinstance(source["byte_size"], bool)
                    or not isinstance(source["byte_size"], int)
                    or not 1 <= source["byte_size"] <= _MAX_SOURCE_BYTES
                    or not _SHA256.fullmatch(source["content_sha256"])
                    or not isinstance(source["stored_name"], str)
                    or Path(source["stored_name"]).name != source["stored_name"]
                    or not source["stored_name"].startswith(source["content_sha256"])
                    or not 64 <= len(source["stored_name"]) <= 80
                    or source_instruction_risk[object_id] not in {0, 1}
                    or not isinstance(source["warnings"], list)
                    or len(source["warnings"]) > 64
                    or any(
                        not isinstance(warning, str)
                        or warning != warning.strip()
                        or not 1 <= len(warning) <= 500
                        for warning in source["warnings"]
                    )
                    or source["compiler"].get("schema_version") != "deeplaw.knowledge-compiler/v1"
                    or source["compiler"].get("source_sha256") != source["content_sha256"]
                    or len(canonical_json(source["compiler"]).encode("utf-8")) > _MAX_COMPILER_BYTES
                ):
                    return failed("source_state_mismatch", object_id)
                try:
                    canonical_timestamp(
                        source["imported_at"],
                        field="source imported_at",
                    )
                except (TypeError, ValueError):
                    return failed("source_timestamp_invalid", object_id)
                identity_v2_source = payload_fields == frozenset(identity_v2_payload)
                if payload_fields == frozenset(legacy_payload):
                    fragment_ids = payload["fragment_ids"]
                    asset_ids = payload["asset_ids"]
                    if not isinstance(fragment_ids, list) or not isinstance(
                        asset_ids,
                        list,
                    ):
                        return failed("source_compiled_event_invalid", object_id)
                elif identity_v2_source:
                    ordered_fragments = sorted(
                        fragment_ids_by_source.get(object_id, []),
                    )
                    fragment_ids = [fragment_id for _, fragment_id in ordered_fragments]
                    fragment_inventory: list[dict[str, Any]] = []
                    fragment_inventory_valid = True
                    for fragment_id in fragment_ids:
                        canonical_fragment = identity_fragments.get(fragment_id)
                        if canonical_fragment is None or not canonical_fragment[
                            "logical_node_keys"
                        ]:
                            fragment_inventory_valid = False
                            continue
                        fragment_inventory.append(
                            {
                                "ordinal": canonical_fragment["ordinal"],
                                "locator": canonical_fragment["locator"],
                                "text_sha256": canonical_fragment["text_sha256"],
                                "instruction_risk": canonical_fragment["instruction_risk"],
                                "logical_node_keys": canonical_fragment[
                                    "logical_node_keys"
                                ],
                            }
                        )
                    source_revision = self.connection.execute(
                        "SELECT * FROM source_revisions_v2 WHERE source_revision_id = ?",
                        (payload["source_revision_id"],),
                    ).fetchone()
                    compilation = self.connection.execute(
                        "SELECT * FROM compilations_v2 WHERE compilation_id = ?",
                        (payload["compilation_id"],),
                    ).fetchone()
                    proposal_set = self.connection.execute(
                        "SELECT * FROM proposal_sets_v2 WHERE proposal_set_id = ?",
                        (payload["proposal_set_id"],),
                    ).fetchone()
                    source_binding = self.connection.execute(
                        "SELECT source_revision_id FROM source_revision_bindings_v2 "
                        "WHERE legacy_source_id = ?",
                        (object_id,),
                    ).fetchone()
                    build_binding = self.connection.execute(
                        "SELECT compilation_id, proposal_set_id "
                        "FROM source_build_bindings_v2 WHERE legacy_source_id = ?",
                        (object_id,),
                    ).fetchone()
                    member_rows = self.connection.execute(
                        """
                        SELECT legacy_asset_id
                        FROM asset_revision_bindings_v2
                        WHERE legacy_source_id = ? AND proposal_set_id = ?
                        ORDER BY proposal_ordinal
                        """,
                        (object_id, payload["proposal_set_id"]),
                    ).fetchall()
                    asset_ids = [row["legacy_asset_id"] for row in member_rows]
                    if (
                        source_revision is None
                        or compilation is None
                        or proposal_set is None
                        or not fragment_inventory_valid
                        or source_binding is None
                        or source_binding["source_revision_id"] != payload["source_revision_id"]
                        or source_revision["source_key"] != payload["source_key"]
                        or source_revision["content_sha256"] != payload["source_sha256"]
                        or compilation["source_revision_id"] != payload["source_revision_id"]
                        or proposal_set["compilation_id"] != payload["compilation_id"]
                        or build_binding is None
                        or build_binding["compilation_id"] != payload["compilation_id"]
                        or build_binding["proposal_set_id"] != payload["proposal_set_id"]
                        or isinstance(payload["fragment_count"], bool)
                        or not isinstance(payload["fragment_count"], int)
                        or payload["fragment_count"] != len(fragment_ids)
                        or payload["fragment_inventory_sha256"]
                        != sha256_bytes(canonical_json(fragment_inventory).encode("utf-8"))
                        or compilation["fragment_inventory_sha256"]
                        != payload["fragment_inventory_sha256"]
                        or isinstance(payload["proposal_count"], bool)
                        or not isinstance(payload["proposal_count"], int)
                        or payload["proposal_count"] != len(asset_ids)
                        or payload["asset_count"] != payload["proposal_count"]
                        or payload["membership_sha256"]
                        != sha256_bytes(
                            canonical_json(
                                {
                                    "fragment_inventory_sha256": payload[
                                        "fragment_inventory_sha256"
                                    ],
                                    "proposal_inventory_sha256": payload[
                                        "proposal_inventory_sha256"
                                    ],
                                    "proposal_ref_graph_sha256": payload[
                                        "proposal_ref_graph_sha256"
                                    ],
                                }
                            ).encode("utf-8")
                        )
                        or proposal_set["proposal_inventory_sha256"]
                        != payload["proposal_inventory_sha256"]
                        or proposal_set["proposal_ref_graph_sha256"]
                        != payload["proposal_ref_graph_sha256"]
                    ):
                        return failed("source_compiled_identity_v2_invalid", object_id)
                else:
                    ordered_fragments = sorted(
                        fragment_ids_by_source.get(object_id, []),
                    )
                    fragment_ids = [fragment_id for _, fragment_id in ordered_fragments]
                    if (
                        isinstance(payload["fragment_count"], bool)
                        or not isinstance(payload["fragment_count"], int)
                        or isinstance(payload["asset_count"], bool)
                        or not isinstance(payload["asset_count"], int)
                        or payload["fragment_count"] != len(fragment_ids)
                        or payload["asset_count"] != len(fragment_ids)
                        or not isinstance(payload["membership_sha256"], str)
                        or not _SHA256.fullmatch(payload["membership_sha256"])
                        or any(
                            fragment_id in duplicate_asset_fragments
                            or fragment_id not in asset_id_by_fragment
                            for fragment_id in fragment_ids
                        )
                    ):
                        return failed("source_compiled_membership_invalid", object_id)
                    asset_ids = [asset_id_by_fragment[fragment_id] for fragment_id in fragment_ids]
                    if payload["membership_sha256"] != _source_membership_sha256(
                        fragment_ids,
                        asset_ids,
                    ):
                        return failed("source_compiled_membership_invalid", object_id)
                if (
                    not fragment_ids
                    or len(fragment_ids) != len(set(fragment_ids))
                    or (not identity_v2_source and len(asset_ids) != len(fragment_ids))
                    or len(asset_ids) != len(set(asset_ids))
                    or any(
                        not isinstance(fragment_id, str)
                        or fragment_id not in fragments
                        or fragment_id in expected_fragments
                        for fragment_id in fragment_ids
                    )
                    or any(
                        not isinstance(asset_id, str)
                        or asset_id not in assets
                        or asset_id in expected_assets
                        for asset_id in asset_ids
                    )
                ):
                    return failed("source_compiled_membership_invalid", object_id)
                compiled_sections: list[dict[str, Any]] = []
                if identity_v2_source:
                    for ordinal, fragment_id in enumerate(fragment_ids, start=1):
                        fragment = fragments[fragment_id]
                        text_hash = sha256_bytes(fragment["text"].encode("utf-8"))
                        expected_fragment_id = stable_id(
                            "fragment",
                            object_id,
                            str(ordinal),
                            fragment["locator"],
                            text_hash,
                        )
                        identity_fragment = identity_fragments.get(fragment_id)
                        node = (
                            identity_fragment
                            if identity_fragment is not None
                            and identity_fragment["compilation_id"]
                            == payload["compilation_id"]
                            else None
                        )
                        if (
                            fragment["source_id"] != object_id
                            or fragment["ordinal"] != ordinal
                            or not isinstance(fragment["locator"], str)
                            or fragment["locator"] != fragment["locator"].strip()
                            or not 1 <= len(fragment["locator"]) <= 2_000
                            or not isinstance(fragment["text"], str)
                            or fragment["text"] != fragment["text"].strip()
                            or not 1 <= len(fragment["text"]) <= _MAX_FRAGMENT_CHARS
                            or fragment["instruction_risk"] not in {0, 1}
                            or fragment["text_sha256"] != text_hash
                            or expected_fragment_id != fragment_id
                            or node is None
                        ):
                            return failed("source_fragment_binding_mismatch", fragment_id)
                        compiled_sections.append(
                            {
                                "title": node["title"],
                                "locator": fragment["locator"],
                                "text": fragment["text"],
                                "instruction_risk": bool(fragment["instruction_risk"]),
                            }
                        )
                        expected_fragments.add(fragment_id)
                    for asset_id in asset_ids:
                        asset = assets[asset_id]
                        if (
                            not asset.source_refs
                            or not any(
                                reference.source_id == object_id for reference in asset.source_refs
                            )
                            or any(
                                reference.fragment_id not in fragments
                                or fragments[reference.fragment_id]["source_id"]
                                != reference.source_id
                                or fragments[reference.fragment_id]["locator"] != reference.locator
                                or fragments[reference.fragment_id]["text_sha256"]
                                != reference.quote_sha256
                                for reference in asset.source_refs
                            )
                        ):
                            return failed("source_proposal_binding_mismatch", asset_id)
                        expected_assets[asset_id] = {
                            "status": (
                                "quarantined" if payload["instruction_risk"] else "proposed"
                            ),
                            "verification": "source_bound",
                            "content_sha256": asset.content_sha256,
                            "approved": False,
                        }
                else:
                    for ordinal, (fragment_id, asset_id) in enumerate(
                        zip(fragment_ids, asset_ids, strict=True),
                        start=1,
                    ):
                        fragment = fragments[fragment_id]
                        asset = assets[asset_id]
                        text_hash = sha256_bytes(fragment["text"].encode("utf-8"))
                        expected_fragment_id = stable_id(
                            "fragment",
                            object_id,
                            str(ordinal),
                            fragment["locator"],
                            text_hash,
                        )
                        if (
                            fragment["source_id"] != object_id
                            or fragment["ordinal"] != ordinal
                            or not isinstance(fragment["locator"], str)
                            or fragment["locator"] != fragment["locator"].strip()
                            or not 1 <= len(fragment["locator"]) <= 2_000
                            or not isinstance(fragment["text"], str)
                            or fragment["text"] != fragment["text"].strip()
                            or not 1 <= len(fragment["text"]) <= _MAX_FRAGMENT_CHARS
                            or fragment["instruction_risk"] not in {0, 1}
                            or fragment["text_sha256"] != text_hash
                            or expected_fragment_id != fragment_id
                            or len(asset.source_refs) != 1
                            or asset.source_refs[0].source_id != object_id
                            or asset.source_refs[0].fragment_id != fragment_id
                            or asset.source_refs[0].locator != fragment["locator"]
                            or asset.source_refs[0].quote_sha256 != text_hash
                            or asset.statement != fragment["text"]
                        ):
                            return failed("source_fragment_binding_mismatch", fragment_id)
                        compiled_sections.append(
                            {
                                "title": asset.title,
                                "locator": fragment["locator"],
                                "text": fragment["text"],
                                "instruction_risk": bool(fragment["instruction_risk"]),
                            }
                        )
                        expected_assets[asset_id] = {
                            "status": (
                                "quarantined" if payload["instruction_risk"] else "proposed"
                            ),
                            "verification": "source_bound",
                            "content_sha256": asset.content_sha256,
                            "approved": False,
                        }
                        expected_fragments.add(fragment_id)
                if payload["compiler"].get("compiled_fragment_sha256") != sha256_bytes(
                    canonical_json(compiled_sections).encode("utf-8")
                ):
                    return failed("compiled_fragment_digest_mismatch", object_id)
                expected_sources.add(object_id)
                continue

            if event_type == "identity_v2_snapshot":
                if (
                    set(payload) != {"identity_root_sha256", "reason", "source_revision_id"}
                    or object_id != self.vault_id
                    or not isinstance(payload["identity_root_sha256"], str)
                    or not _SHA256.fullmatch(payload["identity_root_sha256"])
                    or payload["reason"]
                    not in {
                        "source_compiled",
                        "identity_migrated",
                        "governance_recorded",
                        "asset_revision_proposed",
                        "relation_recorded",
                        "source_removed",
                    }
                    or (
                        payload["source_revision_id"] is not None
                        and self.connection.execute(
                            "SELECT 1 FROM source_revisions_v2 WHERE source_revision_id = ?",
                            (payload["source_revision_id"],),
                        ).fetchone()
                        is None
                    )
                ):
                    return failed("identity_v2_snapshot_event_invalid", object_id)
                latest_identity_root = payload["identity_root_sha256"]
                continue

            if event_type == "search_index_rebuilt":
                if (
                    object_id is not None
                    or set(payload) != {"tokenizer_profile", "asset_count", "inventory_sha256"}
                    or payload["tokenizer_profile"] != "deeplaw-mixed-cjk-code/2"
                    or isinstance(payload["asset_count"], bool)
                    or not isinstance(payload["asset_count"], int)
                    or not 0 <= payload["asset_count"] <= len(assets)
                    or not isinstance(payload["inventory_sha256"], str)
                    or not _SHA256.fullmatch(payload["inventory_sha256"])
                ):
                    return failed("search_index_rebuild_event_invalid", object_id)
                continue

            if event_type == "knowledge_control_migrated":
                if (
                    set(payload) != {"control_schema", "source_count", "mapping_sha256"}
                    or object_id != self.vault_id
                    or payload["control_schema"] != KNOWLEDGE_CONTROL_SCHEMA
                    or payload["source_count"] != len(expected_sources)
                    or not isinstance(payload["mapping_sha256"], str)
                    or not _SHA256.fullmatch(payload["mapping_sha256"])
                ):
                    return failed("knowledge_control_migration_event_invalid", object_id)
                migrated_lifecycles: dict[str, dict[str, Any]] = {}
                mappings: list[dict[str, Any]] = []
                for source_id in sorted(expected_sources):
                    source_key = stable_id(
                        "sourcekey",
                        self.vault_id,
                        "legacy-source",
                        source_id,
                    )
                    active_asset_ids = [
                        asset_id
                        for asset_id, state in expected_assets.items()
                        if state["status"] == "active"
                        and any(
                            reference.source_id == source_id
                            for reference in assets[asset_id].source_refs
                        )
                    ]
                    activated_at = max(
                        (
                            assets[asset_id].activated_at
                            for asset_id in active_asset_ids
                            if assets[asset_id].activated_at is not None
                        ),
                        default=None,
                    )
                    status = "active" if activated_at is not None else "pending"
                    mappings.append(
                        {
                            "source_id": source_id,
                            "source_key": source_key,
                            "status": status,
                        }
                    )
                    migrated_lifecycles[source_id] = {
                        "source_key": source_key,
                        "previous_source_id": None,
                        "status": status,
                        "activated_at": activated_at,
                        "superseded_at": None,
                        "removed_at": None,
                    }
                if payload["mapping_sha256"] != sha256_bytes(
                    canonical_json(mappings).encode("utf-8")
                ):
                    return failed("knowledge_control_migration_state_mismatch", object_id)
                expected_source_lifecycles = migrated_lifecycles
                continue

            if event_type == "source_activated":
                activation_fields = {
                    "source_key",
                    "previous_source_id",
                    "activated_at",
                    "revoked_asset_count",
                    "revoked_assets_sha256",
                }
                if (
                    set(payload) != activation_fields
                    or not isinstance(object_id, str)
                    or object_id not in expected_source_lifecycles
                    or expected_source_lifecycles[object_id]["status"] != "pending"
                    or payload["source_key"] != expected_source_lifecycles[object_id]["source_key"]
                    or payload["previous_source_id"]
                    != expected_source_lifecycles[object_id]["previous_source_id"]
                    or isinstance(payload["revoked_asset_count"], bool)
                    or not isinstance(payload["revoked_asset_count"], int)
                    or payload["revoked_asset_count"] < 0
                    or not isinstance(payload["revoked_assets_sha256"], str)
                    or not _SHA256.fullmatch(payload["revoked_assets_sha256"])
                ):
                    return failed("source_activation_event_invalid", object_id)
                try:
                    activated_at = canonical_timestamp(
                        payload["activated_at"],
                        field="source activated_at",
                    )
                except (TypeError, ValueError):
                    return failed("source_activation_timestamp_invalid", object_id)
                previous_source_id = payload["previous_source_id"]
                if previous_source_id is not None:
                    previous = expected_source_lifecycles.get(previous_source_id)
                    if previous is None or previous["status"] != "active":
                        return failed("source_activation_predecessor_invalid", object_id)
                    previous["status"] = "superseded"
                    previous["superseded_at"] = activated_at
                expected_source_lifecycles[object_id]["status"] = "active"
                expected_source_lifecycles[object_id]["activated_at"] = activated_at
                continue

            if event_type == "source_removed":
                removal_fields = {
                    "reason",
                    "removed_at",
                    "removed_asset_count",
                    "removed_assets_sha256",
                }
                if (
                    set(payload) != removal_fields
                    or not isinstance(object_id, str)
                    or object_id not in expected_source_lifecycles
                    or expected_source_lifecycles[object_id]["status"] == "removed"
                    or not isinstance(payload["reason"], str)
                    or not 1 <= len(payload["reason"]) <= 2_000
                    or isinstance(payload["removed_asset_count"], bool)
                    or not isinstance(payload["removed_asset_count"], int)
                    or payload["removed_asset_count"] < 0
                    or not isinstance(payload["removed_assets_sha256"], str)
                    or not _SHA256.fullmatch(payload["removed_assets_sha256"])
                ):
                    return failed("source_removal_event_invalid", object_id)
                try:
                    removed_at = canonical_timestamp(
                        payload["removed_at"],
                        field="source removed_at",
                    )
                except (TypeError, ValueError):
                    return failed("source_removal_timestamp_invalid", object_id)
                expected_source_lifecycles[object_id]["status"] = "removed"
                expected_source_lifecycles[object_id]["removed_at"] = removed_at
                continue

            if event_type == "review_recorded":
                if (
                    set(payload)
                    != {
                        "receipt_sha256",
                        "review_manifest_sha256",
                        "source_id",
                        "asset_count",
                    }
                    or not isinstance(object_id, str)
                    or object_id not in review_receipts
                    or object_id in expected_review_receipts
                ):
                    return failed("review_receipt_event_invalid", object_id)
                row = review_receipts[object_id]
                try:
                    proposal_ids = strict_json_loads(row["proposal_ids_json"])
                    asset_hashes = strict_json_loads(row["asset_hashes_json"])
                    decisions = strict_json_loads(row["decisions_json"])
                    reviewed_at = canonical_timestamp(
                        row["reviewed_at"],
                        field="review receipt reviewed_at",
                    )
                    signature = (
                        strict_json_loads(row["signature_json"])
                        if row["signature_json"] is not None
                        else None
                    )
                except (json.JSONDecodeError, TypeError, ValueError):
                    return failed("review_receipt_record_invalid", object_id)
                body = {
                    "schema_version": "deeplaw.knowledge-review-receipt/v1",
                    "vault_id": self.vault_id,
                    "reviewer_id": row["reviewer_id"],
                    "reviewed_at": reviewed_at,
                    "policy_id": row["policy_id"],
                    "source_id": row["source_id"],
                    "proposal_ids": proposal_ids,
                    "asset_hashes": asset_hashes,
                    "decisions": decisions,
                    "reason": row["reason"],
                    "review_manifest_sha256": row["review_manifest_sha256"],
                    "signature": signature,
                }
                receipt_sha256 = sha256_bytes(canonical_json(body).encode("utf-8"))
                if (
                    not isinstance(proposal_ids, list)
                    or (not proposal_ids and row["source_id"] is None)
                    or len(proposal_ids) != len(asset_hashes)
                    or len(proposal_ids) != len(decisions)
                    or any(asset_id not in expected_assets for asset_id in proposal_ids)
                    or any(
                        expected_assets[asset_id]["content_sha256"] != digest
                        for asset_id, digest in zip(proposal_ids, asset_hashes, strict=True)
                    )
                    or any(
                        not isinstance(decision, dict)
                        or set(decision) != {"asset_id", "decision"}
                        or decision.get("asset_id") != asset_id
                        or decision.get("decision") not in {"approve", "reject"}
                        for asset_id, decision in zip(proposal_ids, decisions, strict=True)
                    )
                    or row["source_id"] != payload["source_id"]
                    or row["review_manifest_sha256"] != payload["review_manifest_sha256"]
                    or row["receipt_sha256"] != receipt_sha256
                    or payload["receipt_sha256"] != receipt_sha256
                    or payload["asset_count"] != len(proposal_ids)
                    or stable_id(
                        "review",
                        self.vault_id,
                        receipt_sha256,
                        str(event["sequence"]),
                    )
                    != object_id
                    or not isinstance(row["reviewer_id"], str)
                    or not 1 <= len(row["reviewer_id"]) <= 200
                    or not isinstance(row["policy_id"], str)
                    or not 1 <= len(row["policy_id"]) <= 200
                    or not isinstance(row["reason"], str)
                    or not 1 <= len(row["reason"]) <= 2_000
                    or not _SHA256.fullmatch(row["review_manifest_sha256"])
                ):
                    return failed("review_receipt_state_mismatch", object_id)
                expected_review_receipts.add(object_id)
                continue

            if event_type == "run_receipt_recorded":
                if (
                    set(payload) != {"receipt_sha256"}
                    or not isinstance(object_id, str)
                    or object_id not in run_receipts
                    or object_id in expected_run_receipts
                ):
                    return failed("run_receipt_event_invalid", object_id)
                row = run_receipts[object_id]
                try:
                    run_payload = strict_json_loads(row["payload_json"])
                    canonical_timestamp(
                        row["created_at"],
                        field="run receipt created_at",
                    )
                except (json.JSONDecodeError, TypeError, ValueError):
                    return failed("run_receipt_record_invalid", object_id)
                receipt_sha256 = sha256_bytes(canonical_json(run_payload).encode("utf-8"))
                if (
                    not isinstance(run_payload, dict)
                    or run_payload.get("schema_version") != "deeplaw.knowledge-run-receipt/v1"
                    or run_payload.get("vault_id") != self.vault_id
                    or row["receipt_sha256"] != receipt_sha256
                    or payload["receipt_sha256"] != receipt_sha256
                    or stable_id("run", self.vault_id, receipt_sha256) != object_id
                    or row["created_at"] != run_payload.get("finished_at")
                ):
                    return failed("run_receipt_state_mismatch", object_id)
                expected_run_receipts.add(object_id)
                continue

            if event_type == "feedback_recorded":
                if (
                    set(payload) != {"receipt_sha256", "run_id", "proposal_asset_id"}
                    or not isinstance(object_id, str)
                    or object_id not in feedback_records
                    or object_id in expected_feedback_records
                    or payload["run_id"] not in expected_run_receipts
                    or payload["proposal_asset_id"] not in expected_assets
                ):
                    return failed("feedback_record_event_invalid", object_id)
                row = feedback_records[object_id]
                try:
                    feedback_payload = strict_json_loads(row["payload_json"])
                    canonical_timestamp(
                        row["created_at"],
                        field="feedback record created_at",
                    )
                except (json.JSONDecodeError, TypeError, ValueError):
                    return failed("feedback_record_invalid", object_id)
                receipt_sha256 = sha256_bytes(canonical_json(feedback_payload).encode("utf-8"))
                if (
                    not isinstance(feedback_payload, dict)
                    or feedback_payload.get("schema_version")
                    != "deeplaw.knowledge-feedback-ledger/v1"
                    or feedback_payload.get("vault_id") != self.vault_id
                    or row["run_id"] != payload["run_id"]
                    or feedback_payload.get("run_id") != payload["run_id"]
                    or row["proposal_asset_id"] != payload["proposal_asset_id"]
                    or feedback_payload.get("proposal_asset_id") != payload["proposal_asset_id"]
                    or row["receipt_sha256"] != receipt_sha256
                    or payload["receipt_sha256"] != receipt_sha256
                    or stable_id("feedback", self.vault_id, receipt_sha256) != object_id
                ):
                    return failed("feedback_record_state_mismatch", object_id)
                expected_feedback_records.add(object_id)
                continue

            if event_type == "asset_proposed":
                if (
                    set(payload) != {"content_sha256", "status"}
                    or not isinstance(object_id, str)
                    or object_id not in assets
                    or object_id in expected_assets
                    or payload["status"] not in {"proposed", "quarantined"}
                    or payload["content_sha256"] != assets[object_id].content_sha256
                ):
                    return failed("asset_proposed_event_invalid", object_id)
                expected_assets[object_id] = {
                    "status": payload["status"],
                    "verification": "unverified",
                    "content_sha256": payload["content_sha256"],
                    "approved": False,
                }
                continue

            if event_type in {
                "asset_revision_proposed",
                "projection_edit_proposed",
            }:
                if not isinstance(object_id, str) or object_id not in assets:
                    return failed("asset_revision_proposed_event_invalid", object_id)
                asset = assets[object_id]
                if event_type == "projection_edit_proposed":
                    if set(payload) != {
                        "content_sha256",
                        "predecessor_asset_id",
                        "source_ref_count",
                        "approval_inherited",
                    }:
                        return failed("asset_revision_proposed_event_invalid", object_id)
                    predecessor_ids = [payload["predecessor_asset_id"]]
                    transformation = "edit"
                    lineage_status = "modified"
                    status = "quarantined"
                    verification = "source_bound" if asset.source_refs else "unverified"
                else:
                    if set(payload) != {
                        "content_sha256",
                        "status",
                        "verification",
                        "predecessor_asset_ids",
                        "source_ref_count",
                        "lineage_status",
                        "transformation",
                        "approval_inherited",
                    }:
                        return failed("asset_revision_proposed_event_invalid", object_id)
                    predecessor_ids = payload["predecessor_asset_ids"]
                    transformation = payload["transformation"]
                    lineage_status = payload["lineage_status"]
                    status = payload["status"]
                    verification = payload["verification"]
                expected_shape = {
                    "edit": ("modified", 1, 1),
                    "split": ("split", 1, 1),
                    "merge": ("merged", 2, 100),
                }
                shape = expected_shape.get(transformation)
                if (
                    object_id in expected_assets
                    or payload["content_sha256"] != asset.content_sha256
                    or status != "quarantined"
                    or verification not in {"unverified", "source_bound"}
                    or isinstance(payload["source_ref_count"], bool)
                    or not isinstance(payload["source_ref_count"], int)
                    or payload["source_ref_count"] != len(asset.source_refs)
                    or payload["source_ref_count"] > 100
                    or (verification == "source_bound") != bool(asset.source_refs)
                    or payload["approval_inherited"] is not False
                    or shape is None
                    or lineage_status != shape[0]
                    or not isinstance(predecessor_ids, list)
                    or not shape[1] <= len(predecessor_ids) <= shape[2]
                    or len(predecessor_ids) != len(set(predecessor_ids))
                    or object_id in predecessor_ids
                    or any(
                        not isinstance(predecessor_id, str)
                        or predecessor_id not in expected_assets
                        for predecessor_id in predecessor_ids
                    )
                ):
                    return failed("asset_revision_proposed_event_invalid", object_id)
                if transformation in {"split", "merge"} and any(
                    expected_assets[predecessor_id]["status"]
                    not in {"proposed", "quarantined"}
                    for predecessor_id in predecessor_ids
                ):
                    return failed("asset_revision_predecessor_invalid", object_id)
                expected_assets[object_id] = {
                    "status": status,
                    "verification": verification,
                    "content_sha256": payload["content_sha256"],
                    "approved": False,
                }
                continue

            if event_type == "asset_approved":
                if (
                    set(payload) != {"content_sha256", "supersedes_asset_id"}
                    or not isinstance(object_id, str)
                    or object_id not in expected_assets
                    or expected_assets[object_id]["status"] not in {"proposed", "quarantined"}
                    or payload["content_sha256"] != expected_assets[object_id]["content_sha256"]
                ):
                    return failed("asset_approved_event_invalid", object_id)
                supersedes = payload["supersedes_asset_id"]
                if supersedes is not None:
                    if (
                        not isinstance(supersedes, str)
                        or supersedes not in expected_assets
                        or expected_assets[supersedes]["status"] not in {"active", "superseded"}
                        or assets[supersedes].semantic_key != assets[object_id].semantic_key
                    ):
                        return failed("asset_supersession_event_invalid", object_id)
                    expected_assets[supersedes]["status"] = "superseded"
                expected_assets[object_id]["status"] = "active"
                expected_assets[object_id]["verification"] = "human_verified"
                expected_assets[object_id]["approved"] = True
                continue

            if event_type == "asset_revoked":
                if (
                    set(payload) != {"reason", "content_sha256"}
                    or not isinstance(object_id, str)
                    or object_id not in expected_assets
                    or not isinstance(payload["reason"], str)
                    or not payload["reason"]
                    or len(payload["reason"]) > 2_000
                    or payload["content_sha256"] != expected_assets[object_id]["content_sha256"]
                ):
                    return failed("asset_revoked_event_invalid", object_id)
                expected_assets[object_id]["status"] = "revoked"
                continue

            if event_type == "relation_added":
                relation_fields = {
                    "subject_asset_id",
                    "predicate",
                    "object_asset_id",
                    "evidence_fragment_id",
                }
                if (
                    set(payload) != relation_fields
                    or not isinstance(object_id, str)
                    or object_id in expected_relations
                    or payload["subject_asset_id"] not in expected_assets
                    or payload["object_asset_id"] not in expected_assets
                    or expected_assets[payload["subject_asset_id"]]["status"] != "active"
                    or expected_assets[payload["object_asset_id"]]["status"] != "active"
                    or payload["subject_asset_id"] == payload["object_asset_id"]
                    or payload["predicate"] not in RELATION_PREDICATES
                    or (
                        payload["evidence_fragment_id"] is not None
                        and payload["evidence_fragment_id"] not in expected_fragments
                    )
                    or stable_id(
                        "relation",
                        self.vault_id,
                        payload["subject_asset_id"],
                        payload["predicate"],
                        payload["object_asset_id"],
                        payload["evidence_fragment_id"] or "",
                    )
                    != object_id
                ):
                    return failed("relation_added_event_invalid", object_id)
                expected_relations[object_id] = dict(payload)
                continue

        if set(expected_sources) != set(sources):
            return failed("source_event_inventory_mismatch")
        if self.control_enabled:
            if set(expected_source_lifecycles) != set(source_lifecycles):
                return failed("source_lifecycle_inventory_mismatch")
            for source_id, expected in expected_source_lifecycles.items():
                lifecycle = source_lifecycles[source_id]
                if any(lifecycle[field] != value for field, value in expected.items()):
                    return failed("source_lifecycle_state_mismatch", source_id)
        if self.control_enabled and expected_review_receipts != set(review_receipts):
            return failed("review_receipt_event_inventory_mismatch")
        if self.control_enabled and expected_run_receipts != set(run_receipts):
            return failed("run_receipt_event_inventory_mismatch")
        if self.control_enabled and expected_feedback_records != set(feedback_records):
            return failed("feedback_event_inventory_mismatch")
        if set(expected_fragments) != set(fragments):
            return failed("fragment_event_inventory_mismatch")
        if set(expected_assets) != set(assets):
            return failed("asset_event_inventory_mismatch")
        if set(expected_relations) != set(relations):
            return failed("relation_event_inventory_mismatch")

        for asset_id, expected in expected_assets.items():
            asset = assets[asset_id]
            if (
                asset.status != expected["status"]
                or asset.verification != expected["verification"]
                or asset.content_sha256 != expected["content_sha256"]
                or (asset.activated_at is not None) is not expected["approved"]
            ):
                return failed("asset_lifecycle_state_mismatch", asset_id)
            for reference in asset.source_refs:
                fragment = fragments.get(reference.fragment_id)
                if (
                    fragment is None
                    or fragment["source_id"] != reference.source_id
                    or fragment["locator"] != reference.locator
                    or fragment["text_sha256"] != reference.quote_sha256
                ):
                    return failed("asset_source_reference_mismatch", asset_id)

        for relation_id, expected in expected_relations.items():
            relation = relations[relation_id]
            if (
                relation["subject_asset_id"] != expected["subject_asset_id"]
                or relation["predicate"] != expected["predicate"]
                or relation["object_asset_id"] != expected["object_asset_id"]
                or relation["evidence_fragment_id"] != expected["evidence_fragment_id"]
                or relation["verification"] != "human_verified"
            ):
                return failed("relation_state_mismatch", relation_id)
            try:
                canonical_timestamp(
                    relation["created_at"],
                    field="relation created_at",
                )
            except (TypeError, ValueError):
                return failed("relation_timestamp_invalid", relation_id)

        if self.identity_v2_enabled:
            current_identity = identity_snapshot(self.connection)
            identity_source_count = self.connection.execute(
                "SELECT COUNT(*) FROM source_revisions_v2"
            ).fetchone()[0]
            if identity_source_count and latest_identity_root is None:
                return failed("identity_v2_snapshot_missing")
            if (
                latest_identity_root is not None
                and latest_identity_root != current_identity["identity_root_sha256"]
            ):
                return failed("identity_v2_snapshot_mismatch")

        if counts["search_index_count"] != len(assets):
            return failed("search_index_inventory_mismatch")
        indexed_assets: set[str] = set()
        for row in self.connection.execute(
            """
            SELECT asset_id, title_tokens, statement_tokens, semantic_tokens, tag_tokens
            FROM asset_search
            ORDER BY asset_id
            """
        ):
            asset_id = row["asset_id"]
            asset = assets.get(asset_id)
            if asset is None or asset_id in indexed_assets:
                return failed("search_index_asset_mismatch", asset_id)
            indexed_assets.add(asset_id)
            current_tokens = (
                _token_string(asset.title),
                _token_string(asset.statement),
                _token_string(asset.semantic_key or ""),
                _token_string(" ".join(asset.tags)),
            )
            legacy_tokens = (
                _token_string_v1(asset.title),
                _token_string_v1(asset.statement),
                _token_string_v1(asset.semantic_key or ""),
                _token_string_v1(" ".join(asset.tags)),
            )
            indexed_tokens = (
                row["title_tokens"],
                row["statement_tokens"],
                row["semantic_tokens"],
                row["tag_tokens"],
            )
            if indexed_tokens not in {current_tokens, legacy_tokens}:
                return failed("search_index_content_mismatch", asset_id)
        return {
            "valid": True,
            **counts,
            "reason": None,
            "object_id": None,
        }

    def verify_integrity(self) -> dict[str, Any]:
        file_fingerprint = self._database_file_fingerprint()
        if self.read_only and file_fingerprint != self._opened_database_fingerprint:
            raise RuntimeError(
                "knowledge vault database changed while its read snapshot was pinned"
            )
        cache_key = (
            str(self.database),
            *file_fingerprint,
            self.revision,
            self.audit_head,
        )
        if self._integrity_cache_key == cache_key and self._integrity_cache_value is not None:
            return deepcopy(self._integrity_cache_value)
        with _INTEGRITY_CACHE_LOCK:
            cached = _INTEGRITY_CACHE.get(cache_key)
            if cached is not None:
                _INTEGRITY_CACHE.move_to_end(cache_key)
                self._integrity_cache_key = cache_key
                self._integrity_cache_value = deepcopy(cached)
                return deepcopy(cached)
        audit = self.verify_audit_chain()
        state = (
            self.verify_state_integrity()
            if audit["valid"]
            else {
                "valid": False,
                "reason": "audit_chain_invalid",
                "object_id": None,
            }
        )
        result = {
            "valid": bool(audit["valid"] and state["valid"]),
            "audit": audit,
            "state": state,
        }
        if self._database_file_fingerprint() != file_fingerprint:
            raise RuntimeError("knowledge vault database changed during integrity verification")
        self._integrity_cache_key = cache_key
        self._integrity_cache_value = deepcopy(result)
        with _INTEGRITY_CACHE_LOCK:
            for existing_key in tuple(_INTEGRITY_CACHE):
                if existing_key[0] == str(self.database) and existing_key != cache_key:
                    del _INTEGRITY_CACHE[existing_key]
            _INTEGRITY_CACHE[cache_key] = deepcopy(result)
            _INTEGRITY_CACHE.move_to_end(cache_key)
            while len(_INTEGRITY_CACHE) > _MAX_INTEGRITY_CACHE_ENTRIES:
                _INTEGRITY_CACHE.popitem(last=False)
        return result

    def latest_event_at(self) -> str:
        row = self.connection.execute(
            "SELECT created_at FROM events ORDER BY sequence DESC LIMIT 1"
        ).fetchone()
        if row is None:
            raise RuntimeError("knowledge vault event history is missing")
        return canonical_timestamp(row["created_at"], field="latest event created_at")

    def audit_hash_at(self, revision: int) -> str:
        if isinstance(revision, bool) or not 0 <= revision <= self.revision:
            raise ValueError("knowledge vault revision is outside the audit history")
        row = self.connection.execute(
            "SELECT event_hash FROM events WHERE sequence = ?",
            (revision,),
        ).fetchone()
        if row is None or not _SHA256.fullmatch(row["event_hash"]):
            raise RuntimeError("knowledge vault audit event is missing or invalid")
        return cast(str, row["event_hash"])

    def inspect(self) -> dict[str, Any]:
        status_counts = {
            row["status"]: row["count"]
            for row in self.connection.execute(
                "SELECT status, COUNT(*) AS count FROM assets GROUP BY status"
            ).fetchall()
        }
        tier_counts = {
            row["memory_tier"]: row["count"]
            for row in self.connection.execute(
                """
                SELECT memory_tier, COUNT(*) AS count
                FROM assets WHERE status = 'active'
                GROUP BY memory_tier
                """
            ).fetchall()
        }
        kind_counts = {
            row["kind"]: row["count"]
            for row in self.connection.execute(
                """
                SELECT kind, COUNT(*) AS count
                FROM assets WHERE status = 'active'
                GROUP BY kind
                """
            ).fetchall()
        }
        source_count = self.connection.execute("SELECT COUNT(*) FROM sources").fetchone()[0]
        fragment_count = self.connection.execute(
            "SELECT COUNT(*) FROM source_fragments"
        ).fetchone()[0]
        relation_count = self.connection.execute("SELECT COUNT(*) FROM relations").fetchone()[0]
        expired_count = self.connection.execute(
            """
            SELECT COUNT(*) FROM assets
            WHERE status = 'active' AND expires_at IS NOT NULL AND expires_at <= ?
            """,
            (utc_now(),),
        ).fetchone()[0]
        usable_active_count = self.connection.execute(
            """
            SELECT COUNT(*) FROM assets
            WHERE status = 'active'
              AND verification = 'human_verified'
              AND (expires_at IS NULL OR expires_at > ?)
            """,
            (utc_now(),),
        ).fetchone()[0]
        instruction_risk_count = self.connection.execute(
            "SELECT COUNT(*) FROM sources WHERE instruction_risk = 1"
        ).fetchone()[0]
        integrity = self.verify_integrity()
        source_integrity: dict[str, Any]
        if integrity["valid"]:
            active_source_ids: set[str] = set()
            active_rows = self.connection.execute(
                """
                SELECT * FROM assets
                WHERE status = 'active'
                  AND verification = 'human_verified'
                  AND (expires_at IS NULL OR expires_at > ?)
                ORDER BY asset_id
                """,
                (utc_now(),),
            ).fetchall()
            for row in active_rows:
                active_source_ids.update(
                    reference.source_id for reference in self._row_to_asset(row).source_refs
                )
            source_checks = [
                self._source_file_check(source_id) for source_id in sorted(active_source_ids)
            ]
            invalid_source_ids = [
                check["source_id"] for check in source_checks if not check["valid"]
            ]
            source_integrity = {
                "valid": not invalid_source_ids,
                "checked_active_source_files": len(source_checks),
                "invalid_active_source_file_count": len(invalid_source_ids),
                "invalid_source_ids": invalid_source_ids[:20],
                "invalid_source_ids_truncated": len(invalid_source_ids) > 20,
                "reason": (
                    None
                    if not invalid_source_ids
                    else "active_source_file_missing_or_hash_mismatch"
                ),
            }
        else:
            source_integrity = {
                "valid": False,
                "checked_active_source_files": 0,
                "invalid_active_source_file_count": 0,
                "invalid_source_ids": [],
                "invalid_source_ids_truncated": False,
                "reason": "database_integrity_invalid",
            }
        next_actions: list[str] = []
        proposed = status_counts.get("proposed", 0) + status_counts.get("quarantined", 0)
        if proposed:
            next_actions.append(
                f"review {proposed} proposed/quarantined assets before they can reach Agent context"
            )
        if expired_count:
            next_actions.append(
                f"revoke or supersede {expired_count} expired working-memory assets"
            )
        if not integrity["valid"]:
            next_actions.append("stop using the vault and restore it from a trusted backup")
        elif not source_integrity["valid"]:
            next_actions.append(
                "restore invalid active source files from a trusted content-addressed copy"
            )
        return {
            "schema_version": KNOWLEDGE_VAULT_SCHEMA,
            "storage_schema": KNOWLEDGE_STORAGE_SCHEMA,
            "vault_id": self.vault_id,
            "name": self.manifest["name"],
            "scope": self.manifest["scope"],
            "created_at": self.manifest["created_at"],
            "path": str(self.root),
            "revision": self.revision,
            "audit_head": self.audit_head,
            "audit": integrity["audit"],
            "integrity": integrity,
            "source_integrity": source_integrity,
            "source_count": source_count,
            "fragment_count": fragment_count,
            "relation_count": relation_count,
            "asset_status_counts": {
                status: status_counts.get(status, 0) for status in sorted(ASSET_STATUSES)
            },
            "active_memory_tier_counts": {
                tier: tier_counts.get(tier, 0) for tier in sorted(MEMORY_TIERS)
            },
            "active_kind_counts": {kind: kind_counts.get(kind, 0) for kind in sorted(ASSET_KINDS)},
            "expired_active_count": expired_count,
            "usable_active_count": usable_active_count,
            "instruction_risk_source_count": instruction_risk_count,
            "agent_ready": (
                integrity["valid"] and source_integrity["valid"] and usable_active_count > 0
            ),
            "next_actions": next_actions,
        }

    def all_assets(
        self,
        *,
        statuses: Iterable[str] = ("active",),
    ) -> tuple[KnowledgeAsset, ...]:
        selected = tuple(dict.fromkeys(statuses))
        if not selected or any(status not in ASSET_STATUSES for status in selected):
            raise ValueError("asset status filter is invalid")
        placeholders = ",".join("?" for _ in selected)
        rows = self.connection.execute(
            # selected values are validated enums and remain bound parameters.
            f"SELECT * FROM assets WHERE status IN ({placeholders}) ORDER BY asset_id",
            selected,
        ).fetchall()
        return tuple(self._row_to_asset(row) for row in rows)

    def all_sources(self) -> tuple[dict[str, Any], ...]:
        rows = self.connection.execute(
            "SELECT source_id FROM sources ORDER BY imported_at, source_id"
        ).fetchall()
        return tuple(self.source_info(row["source_id"]) for row in rows)

    def source_versions(self, source_key: str) -> tuple[dict[str, Any], ...]:
        self._require_control()
        if not isinstance(source_key, str) or not _SOURCE_KEY.fullmatch(source_key):
            raise ValueError("knowledge source key is invalid")
        rows = self.connection.execute(
            """
            SELECT source_id FROM source_lifecycle
            WHERE source_key = ?
            ORDER BY source_id
            """,
            (source_key,),
        ).fetchall()
        return tuple(self.source_info(row["source_id"]) for row in rows)

    def source_diff(self, old_source_id: str, new_source_id: str) -> dict[str, Any]:
        self._require_control()
        old = self.source_info(old_source_id)
        new = self.source_info(new_source_id)
        if old["source_key"] != new["source_key"]:
            raise ValueError("source diff requires two versions of the same source key")

        def sections(source_id: str) -> dict[str, dict[str, str]]:
            rows = self.connection.execute(
                """
                SELECT assets.asset_id, assets.semantic_key,
                       source_fragments.text_sha256
                FROM assets
                JOIN json_each(assets.source_refs_json) AS reference
                JOIN source_fragments
                  ON source_fragments.fragment_id =
                     json_extract(reference.value, '$.fragment_id')
                WHERE json_extract(reference.value, '$.source_id') = ?
                ORDER BY assets.asset_id
                """,
                (source_id,),
            ).fetchall()
            return {
                row["semantic_key"] or row["asset_id"]: {
                    "asset_id": row["asset_id"],
                    "text_sha256": row["text_sha256"],
                }
                for row in rows
            }

        old_sections = sections(old_source_id)
        new_sections = sections(new_source_id)
        unchanged = sorted(
            key
            for key in old_sections.keys() & new_sections.keys()
            if old_sections[key]["text_sha256"] == new_sections[key]["text_sha256"]
        )
        changed = sorted(
            key
            for key in old_sections.keys() & new_sections.keys()
            if old_sections[key]["text_sha256"] != new_sections[key]["text_sha256"]
        )
        added = sorted(new_sections.keys() - old_sections.keys())
        removed = sorted(old_sections.keys() - new_sections.keys())
        return {
            "schema_version": "deeplaw.knowledge-source-diff/v1",
            "vault_id": self.vault_id,
            "source_key": old["source_key"],
            "old_source_id": old_source_id,
            "new_source_id": new_source_id,
            "old_content_sha256": old["content_sha256"],
            "new_content_sha256": new["content_sha256"],
            "unchanged_count": len(unchanged),
            "changed_count": len(changed),
            "added_count": len(added),
            "removed_count": len(removed),
            "changed_semantic_keys": changed[:100],
            "added_semantic_keys": added[:100],
            "removed_semantic_keys": removed[:100],
            "details_truncated": any(len(values) > 100 for values in (changed, added, removed)),
        }

    def remove_source(
        self,
        source_id: str,
        *,
        reason: str,
        confirm: bool,
    ) -> dict[str, Any]:
        self._require_write()
        self._require_control()
        if not confirm:
            raise ValueError("source removal requires explicit confirmation")
        reason = reason.strip()
        if not 1 <= len(reason) <= 2_000:
            raise ValueError("source removal reason must be between 1 and 2000 characters")
        try:
            self.connection.execute("BEGIN IMMEDIATE")
            self._require_healthy_integrity()
            source = self.source_info(source_id)
            if source["status"] == "removed":
                self.connection.rollback()
                return {
                    "schema_version": "deeplaw.knowledge-source-removal/v1",
                    "vault_id": self.vault_id,
                    "source_id": source_id,
                    "source_key": source["source_key"],
                    "removed_asset_count": 0,
                    "idempotent": True,
                    "revision": self.revision,
                    "audit_head": self.audit_head,
                }
            removed_at = utc_now()
            if self.identity_v2_enabled:
                removal_inventory_sql = """
                    SELECT DISTINCT assets.asset_id, assets.content_sha256,
                           assets.trust, assets.sensitivity, assets.verification,
                           asset_revision_bindings_v2.asset_revision_id,
                           knowledge_revisions_v2.knowledge_key
                    FROM assets
                    LEFT JOIN asset_revision_bindings_v2
                      ON asset_revision_bindings_v2.legacy_asset_id = assets.asset_id
                    LEFT JOIN knowledge_revisions_v2
                      ON knowledge_revisions_v2.asset_revision_id =
                         asset_revision_bindings_v2.asset_revision_id
                    JOIN json_each(assets.source_refs_json) AS reference
                    WHERE json_extract(reference.value, '$.source_id') = ?
                      AND assets.status IN ('proposed', 'quarantined', 'active')
                    ORDER BY assets.asset_id
                """
            else:
                removal_inventory_sql = """
                    SELECT DISTINCT assets.asset_id, assets.content_sha256,
                           assets.trust, assets.sensitivity, assets.verification,
                           NULL AS asset_revision_id, NULL AS knowledge_key
                    FROM assets
                    JOIN json_each(assets.source_refs_json) AS reference
                    WHERE json_extract(reference.value, '$.source_id') = ?
                      AND assets.status IN ('proposed', 'quarantined', 'active')
                    ORDER BY assets.asset_id
                """
            rows = self.connection.execute(
                removal_inventory_sql,
                (source_id,),
            ).fetchall()
            removed_ids: list[str] = []
            for row in rows:
                self.connection.execute(
                    "UPDATE assets SET status = 'revoked' WHERE asset_id = ?",
                    (row["asset_id"],),
                )
                self._append_event(
                    event_type="asset_revoked",
                    object_id=row["asset_id"],
                    payload={
                        "reason": f"Source removed: {reason}",
                        "content_sha256": row["content_sha256"],
                    },
                )
                if (
                    row["asset_revision_id"] is not None
                    and row["knowledge_key"] is not None
                    and source.get("source_revision_id") is not None
                ):
                    record_lineage_transition(
                        self.connection,
                        knowledge_key=row["knowledge_key"],
                        from_asset_revision_ids=(row["asset_revision_id"],),
                        to_asset_revision_ids=(),
                        status="deleted",
                        source_revision_id=source["source_revision_id"],
                        mapping_evidence={
                            "method": "explicit-source-removal",
                            "source_id": source_id,
                            "reason_sha256": sha256_bytes(reason.encode("utf-8")),
                        },
                        created_at=removed_at,
                    )
                    prior_asset_governance = self.connection.execute(
                        """
                        SELECT recorded_at FROM governance_revisions_v2
                        WHERE subject_kind = 'asset_revision' AND subject_id = ?
                        ORDER BY recorded_at DESC, governance_revision DESC LIMIT 1
                        """,
                        (row["asset_revision_id"],),
                    ).fetchone()
                    asset_governance_recorded_at = (
                        _timestamp_after(
                            removed_at,
                            prior_asset_governance["recorded_at"],
                        )
                        if prior_asset_governance is not None
                        else removed_at
                    )
                    record_governance_revision(
                        self.connection,
                        subject_kind="asset_revision",
                        subject_id=row["asset_revision_id"],
                        trust=row["trust"],
                        sensitivity=row["sensitivity"],
                        policy_id="deeplaw.local-source-removal/v2",
                        review_status=row["verification"],
                        lifecycle_status="deleted",
                        activation_status="inactive",
                        revoked_at=removed_at,
                        export_allowed=False,
                        reviewer_id="local-operator",
                        recorded_at=asset_governance_recorded_at,
                    )
                removed_ids.append(row["asset_id"])
            self.connection.execute(
                """
                UPDATE source_lifecycle
                SET status = 'removed', removed_at = ?
                WHERE source_id = ?
                """,
                (removed_at, source_id),
            )
            if source.get("source_revision_id") is not None:
                prior_source_governance = self.connection.execute(
                    """
                    SELECT review_status, recorded_at FROM governance_revisions_v2
                    WHERE subject_kind = 'source_revision' AND subject_id = ?
                    ORDER BY recorded_at DESC, governance_revision DESC LIMIT 1
                    """,
                    (source["source_revision_id"],),
                ).fetchone()
                source_governance_recorded_at = (
                    _timestamp_after(
                        removed_at,
                        prior_source_governance["recorded_at"],
                    )
                    if prior_source_governance is not None
                    else removed_at
                )
                record_governance_revision(
                    self.connection,
                    subject_kind="source_revision",
                    subject_id=source["source_revision_id"],
                    trust=source["trust"],
                    sensitivity=source["sensitivity"],
                    policy_id="deeplaw.local-source-removal/v2",
                    review_status=(
                        prior_source_governance["review_status"]
                        if prior_source_governance is not None
                        else "unreviewed"
                    ),
                    lifecycle_status="removed",
                    activation_status="inactive",
                    revoked_at=removed_at,
                    export_allowed=False,
                    reviewer_id="local-operator",
                    recorded_at=source_governance_recorded_at,
                )
            revision, audit_head = self._append_event(
                event_type="source_removed",
                object_id=source_id,
                payload={
                    "reason": reason,
                    "removed_at": removed_at,
                    "removed_asset_count": len(removed_ids),
                    "removed_assets_sha256": sha256_bytes(
                        canonical_json(removed_ids).encode("utf-8")
                    ),
                },
            )
            if source.get("source_revision_id") is not None:
                revision, audit_head = self._append_identity_snapshot(
                    reason="source_removed",
                    source_revision_id=source["source_revision_id"],
                )
            self.connection.commit()
        except BaseException:
            self.connection.rollback()
            raise
        return {
            "schema_version": "deeplaw.knowledge-source-removal/v1",
            "vault_id": self.vault_id,
            "source_id": source_id,
            "source_key": source["source_key"],
            "removed_asset_count": len(removed_ids),
            "idempotent": False,
            "revision": revision,
            "audit_head": audit_head,
        }

    def verify_source(self, source_id: str) -> dict[str, Any]:
        source = self.source_info(source_id)
        file_check = self._source_file_check(source_id)
        return {
            "schema_version": "deeplaw.knowledge-source-verification/v1",
            "vault_id": self.vault_id,
            "source": source,
            "file": file_check,
            "database_integrity_valid": self.verify_integrity()["valid"],
            "valid": bool(file_check["valid"] and self.verify_integrity()["valid"]),
        }

    def all_relations(self) -> tuple[dict[str, Any], ...]:
        rows = self.connection.execute("SELECT * FROM relations ORDER BY relation_id").fetchall()
        return tuple(dict(row) for row in rows)
