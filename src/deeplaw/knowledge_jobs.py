from __future__ import annotations

import json
import os
import re
import secrets
from fnmatch import fnmatch
from pathlib import Path
from typing import Any, cast

from .knowledge_compiler import TYPED_EXTRACTION_MODES, compile_source
from .knowledge_identity import (
    canonical_origin_commitment,
    make_collection_id,
    make_source_key,
    normalize_logical_path,
)
from .knowledge_models import (
    SENSITIVITY_LEVELS,
    SOURCE_KINDS,
    USER_SETTABLE_TRUST_LEVELS,
    Sensitivity,
    SourceKind,
    TrustLevel,
    canonical_timestamp,
    utc_now,
)
from .knowledge_store import KnowledgeVault
from .util import canonical_json, sha256_bytes, sha256_file, stable_id, strict_json_loads

INGEST_JOB_SCHEMA_V1 = "deeplaw.knowledge-ingest-job/v1"
INGEST_JOB_SCHEMA = "deeplaw.knowledge-ingest-job/v2"
SOURCE_REGISTRY_SCHEMA = "deeplaw.local-source-registry/v1"
JOB_STATES = frozenset(
    {"planned", "running", "completed", "partial", "cancelled", "interrupted"}
)
ITEM_STATES = frozenset({"pending", "running", "succeeded", "failed", "cancelled"})

_MAX_JOB_BYTES = 32 * 1024 * 1024
_MAX_REGISTRY_BYTES = 32 * 1024 * 1024
_MAX_JOB_ITEMS = 100_000
_MAX_ATTEMPTS = 10
_JOB_ID = re.compile(r"^ingestjob_[0-9a-f]{24}$")
_ITEM_ID = re.compile(r"^jobitem_[0-9a-f]{24}$")
_SOURCE_KEY = re.compile(r"^sourcekey_[0-9a-f]{24}$")
_COLLECTION_ID = re.compile(r"^collection_[0-9a-f]{24}$")
_SOURCE_ID = re.compile(r"^source_[0-9a-f]{24}$")
_SNAPSHOT_ID = re.compile(r"^sourcesnapshot_[0-9a-f]{24}$")
_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_SUPPORTED_SUFFIXES = frozenset(
    {
        ".pdf",
        ".docx",
        ".doc",
        ".pptx",
        ".xlsx",
        ".epub",
        ".txt",
        ".md",
        ".markdown",
        ".json",
        ".jsonl",
        ".csv",
        ".tsv",
        ".py",
        ".pyi",
        ".js",
        ".jsx",
        ".ts",
        ".tsx",
        ".go",
        ".rs",
        ".java",
        ".kt",
        ".swift",
        ".c",
        ".h",
        ".cpp",
        ".hpp",
        ".sql",
        ".yaml",
        ".yml",
        ".toml",
        ".xml",
        ".html",
        ".htm",
        ".css",
        ".log",
    }
)


def _owner_directory(path: Path) -> Path:
    if path.is_symlink() or (path.exists() and not path.is_dir()):
        raise RuntimeError("knowledge operation directory is unsafe")
    path.mkdir(parents=True, exist_ok=True, mode=0o700)
    if os.name != "nt":
        os.chmod(path, 0o700)
    return path


def _operations_root(vault: KnowledgeVault, *, create: bool = True) -> Path:
    root = vault.root / "operations"
    if create:
        _owner_directory(root)
        _owner_directory(root / "jobs")
    return root


def _write_json(path: Path, value: dict[str, Any], *, maximum: int) -> None:
    if path.is_symlink() or (path.exists() and not path.is_file()):
        raise RuntimeError("knowledge operation record path is unsafe")
    payload = (json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n").encode()
    if not 1 <= len(payload) <= maximum:
        raise ValueError("knowledge operation record exceeds its size bound")
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


def _read_json(path: Path, *, maximum: int) -> dict[str, Any]:
    if (
        path.is_symlink()
        or not path.is_file()
        or not 1 <= path.stat().st_size <= maximum
        or (os.name != "nt" and path.stat().st_mode & 0o077)
    ):
        raise RuntimeError("knowledge operation record is missing or unsafe")
    value = strict_json_loads(path.read_bytes())
    if not isinstance(value, dict):
        raise RuntimeError("knowledge operation record must be an object")
    return value


def _job_digest(job: dict[str, Any]) -> str:
    return sha256_bytes(
        canonical_json(
            {key: value for key, value in job.items() if key != "record_sha256"}
        ).encode()
    )


def _write_job(vault: KnowledgeVault, job: dict[str, Any]) -> None:
    job["updated_at"] = utc_now()
    if isinstance(job.get("items"), list):
        job["summary"] = _job_summary(job["items"])
    job["record_sha256"] = _job_digest(job)
    _write_json(
        _operations_root(vault) / "jobs" / f"{job['job_id']}.json",
        job,
        maximum=_MAX_JOB_BYTES,
    )


def _valid_configuration(value: Any) -> bool:
    expected = {
        "source_kind",
        "trust",
        "sensitivity",
        "pdf_fallback",
        "typed_extraction",
        "typed_extractor_manifest_hint",
        "confirm_external_disclosure",
        "reference_proposals",
    }
    return (
        isinstance(value, dict)
        and set(value) == expected
        and value.get("source_kind") in SOURCE_KINDS
        and value.get("trust") in USER_SETTABLE_TRUST_LEVELS
        and value.get("sensitivity") in SENSITIVITY_LEVELS
        and value.get("pdf_fallback") in {"off", "vision-consensus", "document-engine"}
        and value.get("typed_extraction") in TYPED_EXTRACTION_MODES
        and (
            value.get("typed_extractor_manifest_hint") is None
            or (
                isinstance(value["typed_extractor_manifest_hint"], str)
                and 1 <= len(value["typed_extractor_manifest_hint"]) <= 4_096
            )
        )
        and isinstance(value.get("confirm_external_disclosure"), bool)
        and isinstance(value.get("reference_proposals"), bool)
    )


def _valid_registration(value: Any) -> bool:
    expected = {
        "enabled",
        "root_path_hint",
        "single_file",
        "recursive",
        "include",
        "exclude",
    }
    return (
        isinstance(value, dict)
        and set(value) == expected
        and isinstance(value.get("enabled"), bool)
        and isinstance(value.get("root_path_hint"), str)
        and 1 <= len(value["root_path_hint"]) <= 4_096
        and isinstance(value.get("single_file"), bool)
        and isinstance(value.get("recursive"), bool)
        and isinstance(value.get("include"), list)
        and isinstance(value.get("exclude"), list)
        and len(value["include"]) <= 32
        and len(value["exclude"]) <= 32
        and all(
            isinstance(pattern, str) and 1 <= len(pattern) <= 500
            for pattern in (*value["include"], *value["exclude"])
        )
    )


def _validate_job(job: dict[str, Any], *, vault_id: str) -> None:
    expected = {
        "schema_version",
        "job_id",
        "vault_id",
        "state",
        "created_at",
        "updated_at",
        "configuration",
        "registration",
        "cancel_requested",
        "items",
        "summary",
        "record_sha256",
    }
    if (
        set(job) != expected
        or job.get("schema_version") not in {INGEST_JOB_SCHEMA_V1, INGEST_JOB_SCHEMA}
        or job.get("vault_id") != vault_id
        or not isinstance(job.get("job_id"), str)
        or not _JOB_ID.fullmatch(job["job_id"])
        or job.get("state") not in JOB_STATES
        or not _valid_configuration(job.get("configuration"))
        or not _valid_registration(job.get("registration"))
        or not isinstance(job.get("cancel_requested"), bool)
        or not isinstance(job.get("items"), list)
        or not 1 <= len(job["items"]) <= _MAX_JOB_ITEMS
        or not isinstance(job.get("summary"), dict)
        or set(job["summary"]) != ITEM_STATES
        or any(
            isinstance(count, bool) or not isinstance(count, int) or count < 0
            for count in job["summary"].values()
        )
        or job.get("record_sha256") != _job_digest(job)
    ):
        raise RuntimeError("knowledge ingest job does not match its closed contract")
    try:
        timestamps_valid = (
            canonical_timestamp(job["created_at"], field="job creation time")
            == job["created_at"]
            and canonical_timestamp(job["updated_at"], field="job update time")
            == job["updated_at"]
            and job["created_at"] <= job["updated_at"]
        )
    except (TypeError, ValueError) as error:
        raise RuntimeError("knowledge ingest job timestamps are invalid") from error
    if not timestamps_valid:
        raise RuntimeError("knowledge ingest job timestamps are not canonical")
    v2 = job["schema_version"] == INGEST_JOB_SCHEMA
    seen: set[str] = set()
    for item in job["items"]:
        expected_item = {
            "item_id",
            "action",
            "path_hint",
            "logical_path",
            "source_key",
            "planned_sha256",
            "planned_byte_size",
            "state",
            "attempts",
            "source_id",
            "error",
        }
        if v2:
            expected_item.update({"collection_id", "origin_uri", "snapshot_id"})
        if (
            not isinstance(item, dict)
            or set(item) != expected_item
            or item.get("item_id") in seen
            or item.get("action") not in {"add", "update", "move"}
            or not isinstance(item.get("item_id"), str)
            or not _ITEM_ID.fullmatch(item["item_id"])
            or not isinstance(item.get("path_hint"), str)
            or not 1 <= len(item["path_hint"]) <= 4_096
            or not Path(item["path_hint"]).is_absolute()
            or not isinstance(item.get("logical_path"), str)
            or not isinstance(item.get("source_key"), str)
            or not _SOURCE_KEY.fullmatch(item["source_key"])
            or not isinstance(item.get("planned_sha256"), str)
            or not _SHA256.fullmatch(item["planned_sha256"])
            or not isinstance(item.get("planned_byte_size"), int)
            or isinstance(item.get("planned_byte_size"), bool)
            or item["planned_byte_size"] < 0
            or item.get("state") not in ITEM_STATES
            or not isinstance(item.get("attempts"), int)
            or isinstance(item.get("attempts"), bool)
            or not 0 <= item["attempts"] <= _MAX_ATTEMPTS
            or (
                item.get("source_id") is not None
                and (
                    not isinstance(item["source_id"], str)
                    or not _SOURCE_ID.fullmatch(item["source_id"])
                )
            )
            or (item["state"] == "succeeded") != (item.get("source_id") is not None)
            or (
                item.get("error") is not None
                and (
                    not isinstance(item["error"], str)
                    or len(item["error"]) > 2_000
                )
            )
        ):
            raise RuntimeError("knowledge ingest job item is invalid")
        try:
            logical_path = normalize_logical_path(item["logical_path"])
        except ValueError as error:
            raise RuntimeError("knowledge ingest job logical path is invalid") from error
        if logical_path != item["logical_path"]:
            raise RuntimeError("knowledge ingest job logical path is not canonical")
        if v2:
            origin_uri = item.get("origin_uri")
            snapshot_id = item.get("snapshot_id")
            try:
                canonical_origin = (
                    None
                    if origin_uri is None
                    else canonical_origin_commitment(origin_uri)
                )
            except (TypeError, ValueError) as error:
                raise RuntimeError("knowledge ingest job origin is invalid") from error
            if (
                not isinstance(item.get("collection_id"), str)
                or not _COLLECTION_ID.fullmatch(item["collection_id"])
                or canonical_origin != origin_uri
                or (origin_uri is None) != (snapshot_id is None)
                or (
                    snapshot_id is not None
                    and (
                        not isinstance(snapshot_id, str)
                        or not _SNAPSHOT_ID.fullmatch(snapshot_id)
                    )
                )
                or (
                    item["action"] != "move"
                    and item["source_key"]
                    != make_source_key(
                        collection_id=item["collection_id"],
                        logical_path=item["logical_path"],
                    )
                )
                or (
                    snapshot_id is not None
                    and isinstance(origin_uri, str)
                    and origin_uri.startswith("https://")
                    and (
                        job["configuration"]["source_kind"] != "web"
                        or job["configuration"]["trust"] != "untrusted"
                    )
                )
            ):
                raise RuntimeError("knowledge ingest job connector identity is invalid")
        seen.add(item["item_id"])
    if job["summary"] != _job_summary(job["items"]):
        raise RuntimeError("knowledge ingest job summary does not match its items")


def _normalize_job_v2(job: dict[str, Any], *, vault_id: str) -> dict[str, Any]:
    if job["schema_version"] == INGEST_JOB_SCHEMA:
        return job
    collection_id = make_collection_id(vault_id=vault_id, name="project")
    normalized = json.loads(json.dumps(job))
    normalized["schema_version"] = INGEST_JOB_SCHEMA
    for item in normalized["items"]:
        item["collection_id"] = collection_id
        item["origin_uri"] = None
        item["snapshot_id"] = None
    normalized["record_sha256"] = _job_digest(normalized)
    return normalized


def _job_path(vault: KnowledgeVault, job_id: str) -> Path:
    if not isinstance(job_id, str) or not job_id.startswith("ingestjob_"):
        raise ValueError("knowledge ingest job ID is invalid")
    return _operations_root(vault, create=False) / "jobs" / f"{job_id}.json"


def load_ingest_job(vault: KnowledgeVault, job_id: str) -> dict[str, Any]:
    job = _read_json(_job_path(vault, job_id), maximum=_MAX_JOB_BYTES)
    _validate_job(job, vault_id=vault.vault_id)
    return _normalize_job_v2(job, vault_id=vault.vault_id)


def _selected_files(
    source: Path,
    *,
    recursive: bool,
    include: tuple[str, ...],
    exclude: tuple[str, ...],
) -> tuple[Path, list[tuple[Path, str]]]:
    candidate = source.expanduser().absolute()
    if candidate.is_symlink():
        raise ValueError("ingest source must not be a symbolic link")
    resolved = candidate.resolve(strict=True)
    if resolved.is_file():
        if resolved.suffix.lower() not in _SUPPORTED_SUFFIXES:
            raise ValueError(f"unsupported ingest file type: {resolved.suffix or '<none>'}")
        return resolved.parent, [(resolved, normalize_logical_path(resolved.name))]
    if not resolved.is_dir():
        raise ValueError("ingest source must be a regular file or directory")
    iterator = resolved.rglob("*") if recursive else resolved.glob("*")
    values: list[tuple[Path, str]] = []
    for path in iterator:
        if path.is_symlink():
            raise ValueError(f"ingest directory contains a symbolic link: {path.name}")
        if not path.is_file() or path.suffix.lower() not in _SUPPORTED_SUFFIXES:
            continue
        relative = path.relative_to(resolved).as_posix()
        if include and not any(fnmatch(relative, pattern) for pattern in include):
            continue
        if any(fnmatch(relative, pattern) for pattern in exclude):
            continue
        values.append((path, normalize_logical_path(relative)))
        if len(values) > _MAX_JOB_ITEMS:
            raise ValueError("ingest directory exceeds the 100000-file job bound")
    values.sort(key=lambda item: item[1])
    if not values:
        raise ValueError("ingest selection contains no supported regular files")
    return resolved, values


def _configuration(
    *,
    source_kind: SourceKind,
    trust: TrustLevel,
    sensitivity: Sensitivity,
    pdf_fallback: str,
    typed_extraction: str,
    typed_extractor_manifest: str | Path | None,
    confirm_external_disclosure: bool,
    reference_proposals: bool,
) -> dict[str, Any]:
    if source_kind not in SOURCE_KINDS:
        raise ValueError("unsupported source kind")
    if trust not in USER_SETTABLE_TRUST_LEVELS:
        raise ValueError("unsupported source trust")
    if sensitivity not in SENSITIVITY_LEVELS:
        raise ValueError("unsupported source sensitivity")
    if typed_extraction not in TYPED_EXTRACTION_MODES:
        raise ValueError("unsupported typed extraction mode")
    manifest_hint = None
    if typed_extractor_manifest is not None:
        manifest_path = Path(typed_extractor_manifest).expanduser().absolute()
        if manifest_path.is_symlink() or not manifest_path.is_file():
            raise ValueError("typed extractor manifest is missing or unsafe")
        manifest_hint = str(manifest_path)
    return {
        "source_kind": source_kind,
        "trust": trust,
        "sensitivity": sensitivity,
        "pdf_fallback": pdf_fallback,
        "typed_extraction": typed_extraction,
        "typed_extractor_manifest_hint": manifest_hint,
        "confirm_external_disclosure": confirm_external_disclosure,
        "reference_proposals": reference_proposals,
    }


def create_ingest_job(
    vault: KnowledgeVault,
    source: str | Path,
    *,
    recursive: bool = True,
    include: tuple[str, ...] = (),
    exclude: tuple[str, ...] = (),
    source_kind: SourceKind = "document",
    trust: TrustLevel = "user_provided",
    sensitivity: Sensitivity = "private",
    pdf_fallback: str = "off",
    typed_extraction: str = "off",
    typed_extractor_manifest: str | Path | None = None,
    confirm_external_disclosure: bool = False,
    reference_proposals: bool = True,
    register_for_sync: bool = True,
) -> dict[str, Any]:
    if vault.read_only:
        raise RuntimeError("ingest jobs require a writable knowledge vault")
    root, selected = _selected_files(
        Path(source), recursive=recursive, include=include, exclude=exclude
    )
    configuration = _configuration(
        source_kind=source_kind,
        trust=trust,
        sensitivity=sensitivity,
        pdf_fallback=pdf_fallback,
        typed_extraction=typed_extraction,
        typed_extractor_manifest=typed_extractor_manifest,
        confirm_external_disclosure=confirm_external_disclosure,
        reference_proposals=reference_proposals,
    )
    collection_id = make_collection_id(vault_id=vault.vault_id, name="project")
    planned: list[dict[str, Any]] = []
    for path, logical_path in selected:
        content_sha256 = sha256_file(path)
        source_key = make_source_key(
            collection_id=collection_id,
            logical_path=logical_path,
        )
        current = vault.active_source_for_key(source_key)
        action = "update" if current is not None else "add"
        planned.append(
            {
                "item_id": stable_id("jobitem", source_key, content_sha256, logical_path),
                "action": action,
                "path_hint": str(path),
                "logical_path": logical_path,
                "source_key": source_key,
                "collection_id": collection_id,
                "origin_uri": None,
                "snapshot_id": None,
                "planned_sha256": content_sha256,
                "planned_byte_size": path.stat().st_size,
                "state": "pending",
                "attempts": 0,
                "source_id": None,
                "error": None,
            }
        )
    created_at = utc_now()
    job_id = stable_id(
        "ingestjob",
        vault.vault_id,
        created_at,
        secrets.token_hex(16),
        sha256_bytes(canonical_json(planned).encode()),
    )
    job = {
        "schema_version": INGEST_JOB_SCHEMA,
        "job_id": job_id,
        "vault_id": vault.vault_id,
        "state": "planned",
        "created_at": created_at,
        "updated_at": created_at,
        "configuration": configuration,
        "registration": {
            "enabled": register_for_sync,
            "root_path_hint": str(root),
            "single_file": Path(source).expanduser().absolute().is_file(),
            "recursive": recursive,
            "include": list(include),
            "exclude": list(exclude),
        },
        "cancel_requested": False,
        "items": planned,
        "summary": _job_summary(planned),
        "record_sha256": "",
    }
    _write_job(vault, job)
    return load_ingest_job(vault, job_id)


def create_snapshot_ingest_job(
    vault: KnowledgeVault,
    snapshots: tuple[dict[str, Any], ...],
    *,
    source_kind: SourceKind,
    trust: TrustLevel,
    sensitivity: Sensitivity,
    pdf_fallback: str = "off",
    typed_extraction: str = "off",
    typed_extractor_manifest: str | Path | None = None,
    confirm_external_disclosure: bool = False,
    reference_proposals: bool = True,
) -> dict[str, Any]:
    """Create a resumable job from immutable, owner-only connector snapshots."""
    if vault.read_only:
        raise RuntimeError("snapshot ingest jobs require a writable knowledge vault")
    if not snapshots or len(snapshots) > _MAX_JOB_ITEMS:
        raise ValueError("snapshot ingest selection is empty or exceeds its bound")
    configuration = _configuration(
        source_kind=source_kind,
        trust=trust,
        sensitivity=sensitivity,
        pdf_fallback=pdf_fallback,
        typed_extraction=typed_extraction,
        typed_extractor_manifest=typed_extractor_manifest,
        confirm_external_disclosure=confirm_external_disclosure,
        reference_proposals=reference_proposals,
    )
    from .source_connectors import verify_source_snapshot

    planned: list[dict[str, Any]] = []
    connector_kinds: set[str] = set()
    for snapshot in snapshots:
        required = {
            "snapshot_id",
            "path_hint",
            "logical_path",
            "collection_id",
            "canonical_origin_uri",
            "content_sha256",
            "byte_size",
        }
        if not required.issubset(snapshot):
            raise ValueError("source snapshot is missing required job fields")
        verified = verify_source_snapshot(vault, str(snapshot["snapshot_id"]))
        connector_kinds.add(verified["connector"])
        if verified["connector"] == "https" and (
            source_kind != "web" or trust != "untrusted"
        ):
            raise ValueError("HTTPS snapshots require web/untrusted ingest governance")
        if any(verified[field] != snapshot[field] for field in required):
            raise RuntimeError("source snapshot fields do not match the verified record")
        path = Path(snapshot["path_hint"])
        if (
            path.is_symlink()
            or not path.is_file()
            or path.stat().st_size != snapshot["byte_size"]
            or sha256_file(path) != snapshot["content_sha256"]
        ):
            raise RuntimeError("source snapshot bytes are missing or changed")
        logical_path = normalize_logical_path(snapshot["logical_path"])
        collection_id = str(snapshot["collection_id"])
        source_key = make_source_key(
            collection_id=collection_id,
            logical_path=logical_path,
        )
        current = vault.active_source_for_key(source_key)
        planned.append(
            {
                "item_id": stable_id(
                    "jobitem",
                    source_key,
                    snapshot["content_sha256"],
                    logical_path,
                    snapshot["snapshot_id"],
                ),
                "action": "update" if current is not None else "add",
                "path_hint": str(path),
                "logical_path": logical_path,
                "source_key": source_key,
                "collection_id": collection_id,
                "origin_uri": str(snapshot["canonical_origin_uri"]),
                "snapshot_id": str(snapshot["snapshot_id"]),
                "planned_sha256": str(snapshot["content_sha256"]),
                "planned_byte_size": int(snapshot["byte_size"]),
                "state": "pending",
                "attempts": 0,
                "source_id": None,
                "error": None,
            }
        )
    if len(connector_kinds) != 1:
        raise ValueError("one snapshot ingest job cannot mix connector kinds")
    planned.sort(key=lambda item: (item["logical_path"], item["snapshot_id"]))
    if len({item["source_key"] for item in planned}) != len(planned):
        raise ValueError("snapshot ingest selection contains duplicate source identities")
    created_at = utc_now()
    job_id = stable_id(
        "ingestjob",
        vault.vault_id,
        created_at,
        secrets.token_hex(16),
        sha256_bytes(canonical_json(planned).encode()),
    )
    job = {
        "schema_version": INGEST_JOB_SCHEMA,
        "job_id": job_id,
        "vault_id": vault.vault_id,
        "state": "planned",
        "created_at": created_at,
        "updated_at": created_at,
        "configuration": configuration,
        "registration": {
            "enabled": False,
            "root_path_hint": str(Path(planned[0]["path_hint"]).parent),
            "single_file": len(planned) == 1,
            "recursive": False,
            "include": [],
            "exclude": [],
        },
        "cancel_requested": False,
        "items": planned,
        "summary": _job_summary(planned),
        "record_sha256": "",
    }
    _write_job(vault, job)
    return load_ingest_job(vault, job_id)


def _job_summary(items: list[dict[str, Any]]) -> dict[str, int]:
    return {
        state: sum(item["state"] == state for item in items)
        for state in sorted(ITEM_STATES)
    }


def _registry_path(vault: KnowledgeVault) -> Path:
    return _operations_root(vault, create=False) / "sources.json"


def _registry_digest(registry: dict[str, Any]) -> str:
    return sha256_bytes(
        canonical_json(
            {key: value for key, value in registry.items() if key != "record_sha256"}
        ).encode()
    )


def _load_registry(vault: KnowledgeVault) -> dict[str, Any]:
    path = _registry_path(vault)
    if not path.exists():
        return {
            "schema_version": SOURCE_REGISTRY_SCHEMA,
            "vault_id": vault.vault_id,
            "updated_at": utc_now(),
            "roots": [],
            "record_sha256": "",
        }
    registry = _read_json(path, maximum=_MAX_REGISTRY_BYTES)
    if (
        set(registry)
        != {"schema_version", "vault_id", "updated_at", "roots", "record_sha256"}
        or registry.get("schema_version") != SOURCE_REGISTRY_SCHEMA
        or registry.get("vault_id") != vault.vault_id
        or not isinstance(registry.get("roots"), list)
        or registry.get("record_sha256") != _registry_digest(registry)
    ):
        raise RuntimeError("local source registry does not match its closed contract")
    return registry


def _write_registry(vault: KnowledgeVault, registry: dict[str, Any]) -> None:
    registry["updated_at"] = utc_now()
    registry["record_sha256"] = _registry_digest(registry)
    _write_json(
        _operations_root(vault) / "sources.json",
        registry,
        maximum=_MAX_REGISTRY_BYTES,
    )


def _update_registry_from_job(vault: KnowledgeVault, job: dict[str, Any]) -> None:
    registration = job["registration"]
    if not registration["enabled"]:
        return
    registry = _load_registry(vault)
    root_hint = registration["root_path_hint"]
    root_id = stable_id("sourceroot", vault.vault_id, root_hint)
    existing = next((root for root in registry["roots"] if root["root_id"] == root_id), None)
    if existing is None:
        existing = {
            "root_id": root_id,
            "path_hint": root_hint,
            "single_file": registration["single_file"],
            "recursive": registration["recursive"],
            "include": registration["include"],
            "exclude": registration["exclude"],
            "configuration": job["configuration"],
            "files": {},
        }
        registry["roots"].append(existing)
    for item in job["items"]:
        if item["state"] != "succeeded":
            continue
        existing["files"][item["logical_path"]] = {
            "path_hint": item["path_hint"],
            "source_key": item["source_key"],
            "source_id": item["source_id"],
            "content_sha256": item["planned_sha256"],
            "byte_size": item["planned_byte_size"],
        }
    registry["roots"].sort(key=lambda item: item["root_id"])
    _write_registry(vault, registry)


def run_ingest_job(
    vault: KnowledgeVault,
    job_id: str,
    *,
    retry_failed: bool = False,
) -> dict[str, Any]:
    if vault.read_only:
        raise RuntimeError("running ingest jobs requires a writable knowledge vault")
    job = load_ingest_job(vault, job_id)
    if retry_failed:
        for item in job["items"]:
            if item["state"] == "failed" and item["attempts"] < _MAX_ATTEMPTS:
                item["state"] = "pending"
                item["error"] = None
    for item in job["items"]:
        if item["state"] == "running":
            item["state"] = "pending"
            item["error"] = "recovered_after_interrupted_process"
    if job["cancel_requested"]:
        job["state"] = "cancelled"
        for item in job["items"]:
            if item["state"] == "pending":
                item["state"] = "cancelled"
        job["summary"] = _job_summary(job["items"])
        _write_job(vault, job)
        return load_ingest_job(vault, job_id)
    job["state"] = "running"
    _write_job(vault, job)
    configuration = job["configuration"]
    for item in job["items"]:
        if item["state"] != "pending":
            continue
        latest = load_ingest_job(vault, job_id)
        if latest["cancel_requested"]:
            job["cancel_requested"] = True
            break
        item["state"] = "running"
        item["attempts"] += 1
        item["error"] = None
        job["summary"] = _job_summary(job["items"])
        _write_job(vault, job)
        try:
            path = Path(item["path_hint"])
            collection_name = "project"
            if item["snapshot_id"] is not None:
                from .source_connectors import (
                    source_snapshot_collection_name,
                    verify_source_snapshot,
                )

                snapshot = verify_source_snapshot(vault, item["snapshot_id"])
                collection_name = source_snapshot_collection_name(snapshot)
                expected_snapshot_fields = {
                    "path_hint": item["path_hint"],
                    "logical_path": item["logical_path"],
                    "collection_id": item["collection_id"],
                    "canonical_origin_uri": item["origin_uri"],
                    "content_sha256": item["planned_sha256"],
                    "byte_size": item["planned_byte_size"],
                }
                if any(
                    snapshot[field] != expected
                    for field, expected in expected_snapshot_fields.items()
                ):
                    raise RuntimeError("source snapshot no longer matches its ingest job")
            if (
                path.is_symlink()
                or not path.is_file()
                or path.stat().st_size != item["planned_byte_size"]
                or sha256_file(path) != item["planned_sha256"]
            ):
                raise RuntimeError("source_changed_after_job_planning")
            result = compile_source(
                vault,
                path,
                source_kind=cast(SourceKind, configuration["source_kind"]),
                trust=cast(TrustLevel, configuration["trust"]),
                sensitivity=cast(Sensitivity, configuration["sensitivity"]),
                confirm_no_case_data=True,
                pdf_fallback=configuration["pdf_fallback"],
                origin_uri=item["origin_uri"],
                source_key=item["source_key"],
                typed_extraction=configuration["typed_extraction"],
                typed_extractor_manifest=configuration[
                    "typed_extractor_manifest_hint"
                ],
                confirm_external_disclosure=configuration[
                    "confirm_external_disclosure"
                ],
                reference_proposals=configuration["reference_proposals"],
                collection_id=item["collection_id"],
                collection_name=collection_name,
                logical_path=item["logical_path"],
            )
            item["state"] = "succeeded"
            item["source_id"] = result["source"]["source_id"]
        except (OSError, RuntimeError, ValueError) as error:
            item["state"] = "failed"
            item["error"] = f"{type(error).__name__}: {str(error)[:1000]}"
        job["summary"] = _job_summary(job["items"])
        _write_job(vault, job)
    if job["cancel_requested"]:
        for item in job["items"]:
            if item["state"] == "pending":
                item["state"] = "cancelled"
        job["state"] = "cancelled"
    elif all(item["state"] == "succeeded" for item in job["items"]):
        job["state"] = "completed"
    elif any(item["state"] == "succeeded" for item in job["items"]):
        job["state"] = "partial"
    else:
        job["state"] = "interrupted"
    job["summary"] = _job_summary(job["items"])
    _write_job(vault, job)
    _update_registry_from_job(vault, job)
    return load_ingest_job(vault, job_id)


def cancel_ingest_job(vault: KnowledgeVault, job_id: str) -> dict[str, Any]:
    job = load_ingest_job(vault, job_id)
    if job["state"] == "completed":
        raise ValueError("a completed ingest job cannot be cancelled")
    job["cancel_requested"] = True
    if job["state"] != "running":
        job["state"] = "cancelled"
        for item in job["items"]:
            if item["state"] == "pending":
                item["state"] = "cancelled"
    job["summary"] = _job_summary(job["items"])
    _write_job(vault, job)
    return load_ingest_job(vault, job_id)


def list_ingest_jobs(vault: KnowledgeVault, *, limit: int = 100) -> dict[str, Any]:
    if isinstance(limit, bool) or not 1 <= limit <= 500:
        raise ValueError("job list limit must be between 1 and 500")
    jobs: list[dict[str, Any]] = []
    for path in sorted(
        (_operations_root(vault, create=False) / "jobs").glob("ingestjob_*.json"),
        key=lambda item: item.name,
        reverse=True,
    ):
        job = _read_json(path, maximum=_MAX_JOB_BYTES)
        _validate_job(job, vault_id=vault.vault_id)
        jobs.append(
            {
                "job_id": job["job_id"],
                "state": job["state"],
                "created_at": job["created_at"],
                "updated_at": job["updated_at"],
                "summary": job["summary"],
            }
        )
        if len(jobs) >= limit:
            break
    return {
        "schema_version": "deeplaw.knowledge-ingest-job-list/v1",
        "vault_id": vault.vault_id,
        "jobs": jobs,
        "returned": len(jobs),
    }


def plan_registered_sync(vault: KnowledgeVault) -> dict[str, Any]:
    registry = _load_registry(vault)
    changes: list[dict[str, Any]] = []
    missing: list[dict[str, Any]] = []
    for root in registry["roots"]:
        root_path = Path(root["path_hint"])
        if root["single_file"]:
            tracked = next(iter(root["files"].values()), None)
            candidates = (
                [(Path(tracked["path_hint"]), next(iter(root["files"])))]
                if tracked
                else []
            )
        else:
            try:
                _, candidates = _selected_files(
                    root_path,
                    recursive=root["recursive"],
                    include=tuple(root["include"]),
                    exclude=tuple(root["exclude"]),
                )
            except (FileNotFoundError, ValueError):
                candidates = []
        current = {logical: path for path, logical in candidates}
        tracked_files = root["files"]
        removed_paths = sorted(set(tracked_files) - set(current))
        added_paths = sorted(set(current) - set(tracked_files))
        added_hashes: dict[str, list[str]] = {}
        for logical_path in added_paths:
            added_hashes.setdefault(sha256_file(current[logical_path]), []).append(logical_path)
        consumed_additions: set[str] = set()
        for old_path in removed_paths:
            tracked = tracked_files[old_path]
            matches = added_hashes.get(tracked["content_sha256"], [])
            if len(matches) == 1:
                new_path = matches[0]
                consumed_additions.add(new_path)
                changes.append(
                    {
                        "action": "move",
                        "root_id": root["root_id"],
                        "path": str(current[new_path]),
                        "logical_path": new_path,
                        "previous_logical_path": old_path,
                        "source_key": tracked["source_key"],
                        "content_sha256": tracked["content_sha256"],
                    }
                )
            else:
                missing.append(
                    {
                        "root_id": root["root_id"],
                        "logical_path": old_path,
                        "source_id": tracked["source_id"],
                        "source_key": tracked["source_key"],
                        "action": "review-removal",
                    }
                )
        for logical_path, path in sorted(current.items()):
            content_sha256 = sha256_file(path)
            tracked = tracked_files.get(logical_path)
            if tracked is None:
                if logical_path in consumed_additions:
                    continue
                changes.append(
                    {
                        "action": "add",
                        "root_id": root["root_id"],
                        "path": str(path),
                        "logical_path": logical_path,
                        "previous_logical_path": None,
                        "source_key": make_source_key(
                            collection_id=make_collection_id(
                                vault_id=vault.vault_id, name="project"
                            ),
                            logical_path=logical_path,
                        ),
                        "content_sha256": content_sha256,
                    }
                )
            elif tracked["content_sha256"] != content_sha256:
                changes.append(
                    {
                        "action": "update",
                        "root_id": root["root_id"],
                        "path": str(path),
                        "logical_path": logical_path,
                        "previous_logical_path": logical_path,
                        "source_key": tracked["source_key"],
                        "content_sha256": content_sha256,
                    }
                )
    return {
        "schema_version": "deeplaw.local-source-sync-plan/v1",
        "vault_id": vault.vault_id,
        "change_count": len(changes),
        "pending_removal_count": len(missing),
        "changes": changes,
        "pending_removals": missing,
        "automatic_removal": False,
    }


def run_registered_sync(vault: KnowledgeVault) -> dict[str, Any]:
    if vault.read_only:
        raise RuntimeError("source sync requires a writable knowledge vault")
    plan = plan_registered_sync(vault)
    if not plan["changes"]:
        return {
            **plan,
            "jobs": [],
            "state": "up-to-date" if not plan["pending_removals"] else "review-required",
        }
    registry = _load_registry(vault)
    roots = {root["root_id"]: root for root in registry["roots"]}
    jobs: list[dict[str, Any]] = []
    for change in plan["changes"]:
        root = roots[change["root_id"]]
        configuration = root["configuration"]
        created = create_ingest_job(
            vault,
            change["path"],
            recursive=False,
            source_kind=cast(SourceKind, configuration["source_kind"]),
            trust=cast(TrustLevel, configuration["trust"]),
            sensitivity=cast(Sensitivity, configuration["sensitivity"]),
            pdf_fallback=configuration["pdf_fallback"],
            typed_extraction=configuration["typed_extraction"],
            typed_extractor_manifest=configuration["typed_extractor_manifest_hint"],
            confirm_external_disclosure=configuration["confirm_external_disclosure"],
            reference_proposals=configuration["reference_proposals"],
            register_for_sync=False,
        )
        job = load_ingest_job(vault, created["job_id"])
        item = job["items"][0]
        item["action"] = change["action"]
        item["logical_path"] = change["logical_path"]
        item["source_key"] = change["source_key"]
        item["item_id"] = stable_id(
            "jobitem",
            item["source_key"],
            item["planned_sha256"],
            item["logical_path"],
        )
        _write_job(vault, job)
        completed = run_ingest_job(vault, job["job_id"])
        jobs.append(
            {
                "job_id": completed["job_id"],
                "state": completed["state"],
                "summary": completed["summary"],
            }
        )
        if completed["state"] == "completed":
            result_item = completed["items"][0]
            if change["action"] == "move":
                root["files"].pop(change["previous_logical_path"], None)
            root["files"][change["logical_path"]] = {
                "path_hint": change["path"],
                "source_key": change["source_key"],
                "source_id": result_item["source_id"],
                "content_sha256": result_item["planned_sha256"],
                "byte_size": result_item["planned_byte_size"],
            }
    _write_registry(vault, registry)
    return {
        **plan,
        "jobs": jobs,
        "state": (
            "completed"
            if all(job["state"] == "completed" for job in jobs)
            and not plan["pending_removals"]
            else "review-required"
            if plan["pending_removals"]
            else "partial"
        ),
    }
