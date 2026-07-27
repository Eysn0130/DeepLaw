from __future__ import annotations

import json
import os
import secrets
import stat
import zipfile
from pathlib import Path, PurePosixPath
from typing import Any, cast

from .knowledge_models import (
    ASSET_KINDS,
    KNOWLEDGE_ASSET_SCHEMA,
    MEMORY_TIERS,
    SENSITIVITY_LEVELS,
    SOURCE_KINDS,
    AssetKind,
    KnowledgeAsset,
    MemoryTier,
    Sensitivity,
    SourceReference,
    canonical_timestamp,
    utc_now,
)
from .knowledge_store import RELATION_PREDICATES, KnowledgeVault
from .util import canonical_json, sha256_bytes, stable_id, strict_json_loads

KNOWLEDGE_PACKAGE_SCHEMA = "deeplaw.knowledge-package/v1"
_MAX_PACKAGE_BYTES = 512 * 1024 * 1024
_MAX_ENTRY_BYTES = 128 * 1024 * 1024
_MAX_RECORDS = 10_000
_MAX_ENTRIES = 20_010
_SENSITIVITY_RANK = {
    "public": 0,
    "internal": 1,
    "private": 2,
    "restricted": 3,
}
_FIXED_ZIP_TIMESTAMP = (1980, 1, 1, 0, 0, 0)


def _is_sha256(value: Any) -> bool:
    return (
        isinstance(value, str)
        and len(value) == 64
        and all(character in "0123456789abcdef" for character in value)
    )


def _jsonl(values: list[dict[str, Any]]) -> bytes:
    return (
        "\n".join(canonical_json(value) for value in values) + ("\n" if values else "")
    ).encode("utf-8")


def _zip_info(name: str) -> zipfile.ZipInfo:
    info = zipfile.ZipInfo(name, date_time=_FIXED_ZIP_TIMESTAMP)
    info.compress_type = zipfile.ZIP_DEFLATED
    info.external_attr = (stat.S_IFREG | 0o600) << 16
    info.create_system = 3
    return info


def _safe_entry(name: str) -> bool:
    path = PurePosixPath(name)
    return (
        bool(name)
        and not name.startswith("/")
        and not name.endswith("/")
        and "\\" not in name
        and ".." not in path.parts
        and path.as_posix() == name
    )


def _selected_source_ids(assets: list[dict[str, Any]]) -> set[str]:
    return {
        reference["source_id"]
        for asset in assets
        for reference in asset.get("source_refs", [])
    }


def _package_basis(manifest: dict[str, Any]) -> dict[str, Any]:
    return {
        "schema_version": manifest["schema_version"],
        "created_at": manifest["created_at"],
        "source_vault": manifest["source_vault"],
        "policy": manifest["policy"],
        "asset_count": manifest["asset_count"],
        "source_count": manifest["source_count"],
        "relation_count": manifest["relation_count"],
        "files": manifest["files"],
    }


def _asset_record_valid(
    item: dict[str, Any],
    *,
    vault_id: str,
    max_sensitivity: str,
) -> bool:
    expected_fields = {
        "schema_version",
        "asset_id",
        "uri",
        "vault_id",
        "kind",
        "memory_tier",
        "title",
        "statement",
        "semantic_key",
        "status",
        "verification",
        "trust",
        "sensitivity",
        "legal_authority",
        "directive_mode",
        "source_refs",
        "tags",
        "warnings",
        "created_at",
        "activated_at",
        "expires_at",
        "supersedes_asset_id",
        "origin_uri",
        "content_sha256",
    }
    try:
        if set(item) != expected_fields or item["schema_version"] != KNOWLEDGE_ASSET_SCHEMA:
            return False
        references = tuple(
            SourceReference(
                source_id=reference["source_id"],
                fragment_id=reference["fragment_id"],
                locator=reference["locator"],
                quote_sha256=reference["quote_sha256"],
            )
            for reference in item["source_refs"]
        )
        asset = KnowledgeAsset(
            asset_id=item["asset_id"],
            vault_id=item["vault_id"],
            kind=item["kind"],
            memory_tier=item["memory_tier"],
            title=item["title"],
            statement=item["statement"],
            semantic_key=item["semantic_key"],
            status=item["status"],
            verification=item["verification"],
            trust=item["trust"],
            sensitivity=item["sensitivity"],
            source_refs=references,
            tags=tuple(item["tags"]),
            warnings=tuple(item["warnings"]),
            created_at=item["created_at"],
            activated_at=item["activated_at"],
            expires_at=item["expires_at"],
            supersedes_asset_id=item["supersedes_asset_id"],
            origin_uri=item["origin_uri"],
            content_sha256=item["content_sha256"],
        )
    except (KeyError, TypeError, ValueError):
        return False
    return (
        asset.vault_id == vault_id
        and item["legal_authority"] is False
        and asset.uri == item["uri"]
        and asset.status == "active"
        and asset.verification == "human_verified"
        and asset.activated_at is not None
        and asset.directive_mode == item["directive_mode"]
        and _SENSITIVITY_RANK[asset.sensitivity]
        <= _SENSITIVITY_RANK[max_sensitivity]
    )


def _source_record_valid(
    item: dict[str, Any],
    *,
    vault_id: str,
    max_sensitivity: str,
) -> bool:
    expected_fields = {
        "source_id",
        "kind",
        "title",
        "origin_uri",
        "media_type",
        "byte_size",
        "content_sha256",
        "trust",
        "sensitivity",
        "imported_at",
        "instruction_risk",
        "warnings",
        "compiler",
    }
    try:
        compiler = item["compiler"]
        expected_id = stable_id(
            "source",
            vault_id,
            item["kind"],
            item["content_sha256"],
            item["title"],
            item["origin_uri"] or "",
            item["trust"],
            item["sensitivity"],
            canonical_json(compiler),
        )
        canonical_timestamp(item["imported_at"], field="source imported_at")
    except (KeyError, TypeError, ValueError):
        return False
    return (
        set(item) == expected_fields
        and item["source_id"] == expected_id
        and isinstance(item["title"], str)
        and bool(item["title"])
        and item["title"] == item["title"].strip()
        and len(item["title"]) <= 500
        and (
            item["origin_uri"] is None
            or (
                isinstance(item["origin_uri"], str)
                and item["origin_uri"] == item["origin_uri"].strip()
                and 1 <= len(item["origin_uri"]) <= 2_000
            )
        )
        and isinstance(item["media_type"], str)
        and item["media_type"] == item["media_type"].strip()
        and 1 <= len(item["media_type"]) <= 200
        and not isinstance(item["byte_size"], bool)
        and isinstance(item["byte_size"], int)
        and 1 <= item["byte_size"] <= _MAX_PACKAGE_BYTES
        and _is_sha256(item["content_sha256"])
        and item["trust"] in {"untrusted", "user_provided", "verified_source"}
        and item["kind"] in SOURCE_KINDS
        and item["sensitivity"] in SENSITIVITY_LEVELS
        and _SENSITIVITY_RANK[item["sensitivity"]]
        <= _SENSITIVITY_RANK[max_sensitivity]
        and isinstance(item["instruction_risk"], bool)
        and isinstance(item["warnings"], list)
        and len(item["warnings"]) <= 64
        and all(
            isinstance(warning, str)
            and warning == warning.strip()
            and 1 <= len(warning) <= 500
            for warning in item["warnings"]
        )
        and isinstance(compiler, dict)
        and compiler.get("schema_version") == "deeplaw.knowledge-compiler/v1"
        and len(canonical_json(compiler).encode("utf-8")) <= 64 * 1024
    )


def _relation_record_valid(item: dict[str, Any], *, vault_id: str) -> bool:
    expected_fields = {
        "relation_id",
        "subject_asset_id",
        "predicate",
        "object_asset_id",
        "evidence_fragment_id",
        "verification",
        "created_at",
    }
    try:
        canonical_timestamp(item["created_at"], field="relation created_at")
        expected_id = stable_id(
            "relation",
            vault_id,
            item["subject_asset_id"],
            item["predicate"],
            item["object_asset_id"],
            item["evidence_fragment_id"] or "",
        )
    except (KeyError, TypeError, ValueError):
        return False
    return (
        set(item) == expected_fields
        and item["relation_id"] == expected_id
        and item["subject_asset_id"] != item["object_asset_id"]
        and item["predicate"] in RELATION_PREDICATES
        and item["verification"] == "human_verified"
        and (
            item["evidence_fragment_id"] is None
            or (
                isinstance(item["evidence_fragment_id"], str)
                and item["evidence_fragment_id"].startswith("fragment_")
            )
        )
    )


def export_knowledge_package(
    vault: KnowledgeVault,
    output: str | Path,
    *,
    max_sensitivity: Sensitivity = "public",
    include_evidence_text: bool = False,
    include_source_files: bool = False,
) -> dict[str, Any]:
    if max_sensitivity not in SENSITIVITY_LEVELS:
        raise ValueError("unsupported export sensitivity")
    if not vault.verify_integrity()["valid"]:
        raise RuntimeError("knowledge vault integrity is invalid; package export stopped")
    if include_source_files and not include_evidence_text:
        raise ValueError("source-file export requires evidence text to be included")
    output_path = Path(output).expanduser().absolute()
    if output_path.is_symlink():
        raise RuntimeError("knowledge package output must not be a symbolic link")
    if output_path.exists():
        raise FileExistsError(
            "knowledge package output already exists; choose a new output path"
        )
    output_path.parent.mkdir(parents=True, exist_ok=True)
    allowed_rank = _SENSITIVITY_RANK[max_sensitivity]
    selected_assets = [
        asset
        for asset in vault.all_assets(statuses=("active",))
        if _SENSITIVITY_RANK[asset.sensitivity] <= allowed_rank
        and (asset.expires_at is None or asset.expires_at > utc_now())
    ]
    if not selected_assets:
        raise ValueError("no active knowledge assets satisfy the export sensitivity policy")
    if len(selected_assets) > _MAX_RECORDS:
        raise ValueError("knowledge package asset count exceeds the export bound")
    source_integrity = vault.verify_source_files(
        reference.source_id
        for asset in selected_assets
        for reference in asset.source_refs
    )
    if not source_integrity["valid"]:
        raise RuntimeError(
            "selected Knowledge Asset source evidence failed integrity verification; "
            "package export stopped"
        )
    asset_payloads = [asset.to_dict() for asset in selected_assets]
    all_sources = {source["source_id"]: source for source in vault.all_sources()}
    relation_payloads = [
        relation
        for relation in vault.all_relations()
        if relation["subject_asset_id"] in {asset.asset_id for asset in selected_assets}
        and relation["object_asset_id"] in {asset.asset_id for asset in selected_assets}
    ]
    if len(relation_payloads) > _MAX_RECORDS:
        raise ValueError("knowledge package relation count exceeds the export bound")
    for relation in relation_payloads:
        evidence_fragment_id = relation["evidence_fragment_id"]
        if evidence_fragment_id is None:
            continue
        evidence_fragment = vault.get_fragment(evidence_fragment_id)
        evidence_source = all_sources[evidence_fragment["source_id"]]
        if _SENSITIVITY_RANK[evidence_source["sensitivity"]] > allowed_rank:
            raise ValueError(
                "knowledge relation evidence exceeds the package sensitivity policy"
            )
    source_ids = _selected_source_ids(asset_payloads)
    fragments: list[dict[str, Any]] = []
    if include_evidence_text:
        fragment_ids = {
            reference.fragment_id
            for asset in selected_assets
            for reference in asset.source_refs
        }
        fragment_ids.update(
            relation["evidence_fragment_id"]
            for relation in relation_payloads
            if relation["evidence_fragment_id"] is not None
        )
        if len(fragment_ids) > _MAX_RECORDS:
            raise ValueError("knowledge package fragment count exceeds the export bound")
        fragments = [vault.get_fragment(fragment_id) for fragment_id in sorted(fragment_ids)]
        source_ids.update(fragment["source_id"] for fragment in fragments)
    source_payloads = [
        {
            key: value
            for key, value in source.items()
            if key
            not in {
                "stored_name",
                "source_key",
                "previous_source_id",
                "status",
                "activated_at",
                "superseded_at",
                "removed_at",
            }
        }
        for source in all_sources.values()
        if source["source_id"] in source_ids
    ]
    if len(source_payloads) > _MAX_RECORDS:
        raise ValueError("knowledge package source count exceeds the export bound")
    payloads = {
        "assets.jsonl": _jsonl(asset_payloads),
        "sources.jsonl": _jsonl(source_payloads),
        "relations.jsonl": _jsonl(relation_payloads),
        "fragments.jsonl": _jsonl(fragments),
    }
    if include_source_files:
        source_by_id = all_sources
        for source_id in sorted(source_ids):
            source = source_by_id[source_id]
            stored_name = source["stored_name"]
            if not stored_name:
                continue
            source_path = vault.source_file_path(source_id)
            if (
                source_path.is_symlink()
                or not source_path.is_file()
                or source_path.stat().st_size != source["byte_size"]
            ):
                raise RuntimeError(f"knowledge source file is missing or unsafe: {source_id}")
            if source_path.stat().st_size > _MAX_ENTRY_BYTES:
                raise ValueError(
                    f"knowledge source file exceeds the portable entry bound: {source_id}"
                )
            payloads[f"source-files/{source_id}/{stored_name}"] = source_path.read_bytes()
    if len(payloads) + 1 > _MAX_ENTRIES:
        raise ValueError("knowledge package entry count exceeds the export bound")
    if any(len(payload) > _MAX_ENTRY_BYTES for payload in payloads.values()):
        raise ValueError("knowledge package payload exceeds the per-entry bound")
    if sum(len(payload) for payload in payloads.values()) > _MAX_PACKAGE_BYTES:
        raise ValueError("knowledge package payload exceeds the expanded-size bound")
    files = [
        {
            "path": name,
            "byte_size": len(payload),
            "sha256": sha256_bytes(payload),
        }
        for name, payload in sorted(payloads.items())
    ]
    manifest: dict[str, Any] = {
        "schema_version": KNOWLEDGE_PACKAGE_SCHEMA,
        "created_at": vault.latest_event_at(),
        "source_vault": {
            "vault_id": vault.vault_id,
            "name": vault.manifest["name"],
            "scope": vault.manifest["scope"],
            "revision": vault.revision,
            "audit_head": vault.audit_head,
        },
        "policy": {
            "max_sensitivity": max_sensitivity,
            "include_evidence_text": include_evidence_text,
            "include_source_files": include_source_files,
            "publisher_identity_verified": False,
            "import_trust": "quarantined",
        },
        "asset_count": len(selected_assets),
        "source_count": len(source_payloads),
        "relation_count": len(relation_payloads),
        "files": files,
    }
    package_id = stable_id(
        "knowledgepkg",
        sha256_bytes(canonical_json(_package_basis(manifest)).encode("utf-8")),
        length=32,
    )
    manifest["package_id"] = package_id
    manifest_bytes = (
        json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    ).encode("utf-8")
    temporary = output_path.with_name(
        f".{output_path.name}.{secrets.token_hex(8)}.tmp"
    )
    descriptor = os.open(temporary, os.O_RDWR | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        with os.fdopen(descriptor, "w+b") as stream:
            with zipfile.ZipFile(
                stream,
                "w",
                compression=zipfile.ZIP_DEFLATED,
                compresslevel=9,
            ) as archive:
                archive.writestr(_zip_info("manifest.json"), manifest_bytes)
                for name, payload in sorted(payloads.items()):
                    archive.writestr(_zip_info(name), payload)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, output_path)
        os.chmod(output_path, 0o600)
    except BaseException:
        temporary.unlink(missing_ok=True)
        raise
    try:
        verification = verify_knowledge_package(output_path)
    except BaseException:
        output_path.unlink(missing_ok=True)
        raise
    if not verification["valid"]:
        output_path.unlink(missing_ok=True)
        raise RuntimeError("exported knowledge package failed its own integrity check")
    return {
        **manifest,
        "path": str(output_path),
        "byte_size": output_path.stat().st_size,
        "content_integrity": "verified by manifest SHA-256; publisher identity is not signed",
    }


def _read_package(path: Path) -> tuple[dict[str, Any], dict[str, bytes]]:
    package_input = path.expanduser().absolute()
    if package_input.is_symlink():
        raise ValueError("knowledge package must be a regular non-symlink file")
    package = package_input.resolve(strict=True)
    if not package.is_file():
        raise ValueError("knowledge package must be a regular non-symlink file")
    if package.stat().st_size > _MAX_PACKAGE_BYTES:
        raise ValueError("knowledge package exceeds the 512 MiB limit")
    try:
        archive = zipfile.ZipFile(package)
    except zipfile.BadZipFile as error:
        raise ValueError("knowledge package is not a valid ZIP archive") from error
    with archive:
        infos = archive.infolist()
        if not 1 <= len(infos) <= _MAX_ENTRIES:
            raise ValueError("knowledge package entry count exceeds the bound")
        names = [info.filename for info in infos]
        if len(set(names)) != len(names) or any(not _safe_entry(name) for name in names):
            raise ValueError("knowledge package contains duplicate or unsafe paths")
        if "manifest.json" not in names:
            raise ValueError("knowledge package manifest is missing")
        payloads: dict[str, bytes] = {}
        total_uncompressed = 0
        for info in infos:
            if info.is_dir() or info.file_size > _MAX_ENTRY_BYTES:
                raise ValueError("knowledge package contains a directory or oversized entry")
            total_uncompressed += info.file_size
            if total_uncompressed > _MAX_PACKAGE_BYTES:
                raise ValueError("knowledge package expanded size exceeds the bound")
            if info.compress_size and info.file_size / info.compress_size > 200:
                raise ValueError("knowledge package contains an unsafe compression ratio")
            payloads[info.filename] = archive.read(info)
    try:
        manifest = strict_json_loads(payloads.pop("manifest.json"))
    except (UnicodeDecodeError, json.JSONDecodeError, ValueError) as error:
        raise ValueError("knowledge package manifest is invalid") from error
    return manifest, payloads


def verify_knowledge_package(path: str | Path) -> dict[str, Any]:
    package_path = Path(path)
    manifest, payloads = _read_package(package_path)
    required = {
        "schema_version",
        "package_id",
        "created_at",
        "source_vault",
        "policy",
        "asset_count",
        "source_count",
        "relation_count",
        "files",
    }
    if not isinstance(manifest, dict) or set(manifest) != required:
        raise ValueError("knowledge package manifest does not match its closed contract")
    if manifest.get("schema_version") != KNOWLEDGE_PACKAGE_SCHEMA:
        raise ValueError("unsupported knowledge package schema")
    try:
        canonical_timestamp(manifest.get("created_at"), field="package created_at")
    except (TypeError, ValueError) as error:
        raise ValueError("knowledge package created_at is invalid") from error
    source_vault = manifest.get("source_vault")
    if not isinstance(source_vault, dict) or set(source_vault) != {
        "vault_id",
        "name",
        "scope",
        "revision",
        "audit_head",
    }:
        raise ValueError("knowledge package source_vault is invalid")
    if (
        not isinstance(source_vault["vault_id"], str)
        or not source_vault["vault_id"].startswith("vault_")
        or not isinstance(source_vault["name"], str)
        or not source_vault["name"]
        or source_vault["name"] != source_vault["name"].strip()
        or len(source_vault["name"]) > 200
        or source_vault["scope"] not in {"personal", "project", "team", "domain"}
        or isinstance(source_vault["revision"], bool)
        or not isinstance(source_vault["revision"], int)
        or source_vault["revision"] < 0
        or not _is_sha256(source_vault["audit_head"])
    ):
        raise ValueError("knowledge package source_vault fields are invalid")
    policy = manifest.get("policy")
    if not isinstance(policy, dict) or set(policy) != {
        "max_sensitivity",
        "include_evidence_text",
        "include_source_files",
        "publisher_identity_verified",
        "import_trust",
    }:
        raise ValueError("knowledge package policy is invalid")
    if (
        policy["max_sensitivity"] not in SENSITIVITY_LEVELS
        or not isinstance(policy["include_evidence_text"], bool)
        or not isinstance(policy["include_source_files"], bool)
        or policy["publisher_identity_verified"] is not False
        or policy["import_trust"] != "quarantined"
        or (policy["include_source_files"] and not policy["include_evidence_text"])
    ):
        raise ValueError("knowledge package policy fields are invalid")
    for count_name in ("asset_count", "source_count", "relation_count"):
        count = manifest.get(count_name)
        minimum = 1 if count_name == "asset_count" else 0
        if (
            isinstance(count, bool)
            or not isinstance(count, int)
            or not minimum <= count <= _MAX_RECORDS
        ):
            raise ValueError(f"knowledge package {count_name} is invalid")
    files = manifest.get("files")
    if not isinstance(files, list) or len(files) != len(payloads):
        raise ValueError("knowledge package file manifest is invalid")
    expected_paths: set[str] = set()
    file_checks: list[dict[str, Any]] = []
    for item in files:
        if not isinstance(item, dict) or set(item) != {"path", "byte_size", "sha256"}:
            raise ValueError("knowledge package file entry is invalid")
        name = item["path"]
        if (
            not isinstance(name, str)
            or not _safe_entry(name)
            or name in expected_paths
            or isinstance(item["byte_size"], bool)
            or not isinstance(item["byte_size"], int)
            or not 0 <= item["byte_size"] <= _MAX_ENTRY_BYTES
            or not _is_sha256(item["sha256"])
        ):
            raise ValueError("knowledge package file path is invalid or duplicated")
        expected_paths.add(name)
        payload = payloads.get(name)
        valid = (
            payload is not None
            and len(payload) == item["byte_size"]
            and sha256_bytes(payload) == item["sha256"]
        )
        file_checks.append({"path": name, "valid": valid})
    expected_id = stable_id(
        "knowledgepkg",
        sha256_bytes(canonical_json(_package_basis(manifest)).encode("utf-8")),
        length=32,
    )
    package_id_valid = manifest.get("package_id") == expected_id
    required_payloads = {
        "assets.jsonl",
        "sources.jsonl",
        "relations.jsonl",
        "fragments.jsonl",
    }
    payload_shape_valid = required_payloads.issubset(payloads) and all(
        name in required_payloads or name.startswith("source-files/")
        for name in payloads
    )
    source_file_policy_valid = policy["include_source_files"] or not any(
        name.startswith("source-files/") for name in payloads
    )
    assets = _parse_jsonl(
        payloads.get("assets.jsonl", b""),
        name="assets.jsonl",
        maximum=_MAX_RECORDS,
    )
    sources = _parse_jsonl(
        payloads.get("sources.jsonl", b""),
        name="sources.jsonl",
        maximum=_MAX_RECORDS,
    )
    relations = _parse_jsonl(
        payloads.get("relations.jsonl", b""),
        name="relations.jsonl",
        maximum=_MAX_RECORDS,
    )
    fragments = _parse_jsonl(
        payloads.get("fragments.jsonl", b""),
        name="fragments.jsonl",
        maximum=_MAX_RECORDS,
    )
    record_counts_valid = (
        len(assets) == manifest["asset_count"]
        and len(sources) == manifest["source_count"]
        and len(relations) == manifest["relation_count"]
        and (policy["include_evidence_text"] or not fragments)
    )
    asset_ids = {
        item.get("asset_id")
        for item in assets
        if isinstance(item.get("asset_id"), str)
    }
    source_by_id = {
        item.get("source_id"): item
        for item in sources
        if isinstance(item.get("source_id"), str)
    }
    fragment_by_id = {
        item.get("fragment_id"): item
        for item in fragments
        if isinstance(item.get("fragment_id"), str)
    }
    asset_records_valid = len(asset_ids) == len(assets) and all(
        _asset_record_valid(
            asset,
            vault_id=source_vault["vault_id"],
            max_sensitivity=policy["max_sensitivity"],
        )
        for asset in assets
    )
    source_records_valid = len(source_by_id) == len(sources) and all(
        _source_record_valid(
            source,
            vault_id=source_vault["vault_id"],
            max_sensitivity=policy["max_sensitivity"],
        )
        for source in sources
    )
    fragment_fields = {
        "fragment_id",
        "source_id",
        "source_title",
        "source_origin_uri",
        "source_sha256",
        "ordinal",
        "locator",
        "text",
        "text_sha256",
        "instruction_risk",
    }
    fragment_records_valid = (
        len(fragment_by_id) == len(fragments)
        and len(
            {
                (fragment.get("source_id"), fragment.get("ordinal"))
                for fragment in fragments
            }
        )
        == len(fragments)
        and all(
            set(fragment) == fragment_fields
            and fragment.get("source_id") in source_by_id
            and source_by_id[fragment["source_id"]].get("title")
            == fragment.get("source_title")
            and source_by_id[fragment["source_id"]].get("origin_uri")
            == fragment.get("source_origin_uri")
            and source_by_id[fragment["source_id"]].get("content_sha256")
            == fragment.get("source_sha256")
            and isinstance(fragment.get("ordinal"), int)
            and not isinstance(fragment.get("ordinal"), bool)
            and fragment["ordinal"] >= 1
            and isinstance(fragment.get("locator"), str)
            and fragment["locator"] == fragment["locator"].strip()
            and 1 <= len(fragment["locator"]) <= 2_000
            and isinstance(fragment.get("text"), str)
            and fragment["text"] == fragment["text"].strip()
            and 1 <= len(fragment["text"]) <= 20_000
            and isinstance(fragment.get("instruction_risk"), bool)
            and fragment.get("text_sha256")
            == sha256_bytes(fragment["text"].encode("utf-8"))
            and fragment.get("fragment_id")
            == stable_id(
                "fragment",
                fragment["source_id"],
                str(fragment["ordinal"]),
                fragment["locator"],
                fragment["text_sha256"],
            )
            for fragment in fragments
        )
    )
    relation_records_valid = all(
        _relation_record_valid(
            relation,
            vault_id=source_vault["vault_id"],
        )
        for relation in relations
    )
    relation_links_valid = relation_records_valid and all(
        relation["subject_asset_id"] in asset_ids
        and relation["object_asset_id"] in asset_ids
        for relation in relations
    )
    asset_source_links_valid = all(
        isinstance(asset.get("source_refs"), list)
        and all(
            isinstance(reference, dict)
            and reference.get("source_id") in source_by_id
            for reference in asset["source_refs"]
        )
        for asset in assets
    )
    evidence_links_valid = True
    if policy["include_evidence_text"]:
        evidence_links_valid = asset_source_links_valid and all(
            isinstance(asset.get("source_refs"), list)
            and all(
                reference.get("fragment_id") in fragment_by_id
                and fragment_by_id[reference["fragment_id"]].get("source_id")
                == reference.get("source_id")
                and fragment_by_id[reference["fragment_id"]].get("text_sha256")
                == reference.get("quote_sha256")
                for reference in asset["source_refs"]
                if isinstance(reference, dict)
            )
            and all(isinstance(reference, dict) for reference in asset["source_refs"])
            for asset in assets
        ) and all(
            relation["evidence_fragment_id"] is None
            or relation["evidence_fragment_id"] in fragment_by_id
            for relation in relations
        )
    source_file_ids: set[str] = set()
    source_files_valid = True
    for name, payload in payloads.items():
        if not name.startswith("source-files/"):
            continue
        parts = name.split("/")
        if len(parts) != 3:
            source_files_valid = False
            continue
        source_id = parts[1]
        source = source_by_id.get(source_id)
        if (
            source is None
            or source_id in source_file_ids
            or source.get("byte_size") != len(payload)
            or source.get("content_sha256") != sha256_bytes(payload)
        ):
            source_files_valid = False
            continue
        source_file_ids.add(source_id)
    if policy["include_source_files"]:
        source_files_valid = source_files_valid and source_file_ids == set(source_by_id)
    valid = (
        expected_paths == set(payloads)
        and all(check["valid"] for check in file_checks)
        and package_id_valid
        and payload_shape_valid
        and source_file_policy_valid
        and record_counts_valid
        and asset_records_valid
        and source_records_valid
        and fragment_records_valid
        and relation_records_valid
        and relation_links_valid
        and asset_source_links_valid
        and evidence_links_valid
        and source_files_valid
    )
    return {
        "schema_version": "deeplaw.knowledge-package-verification/v1",
        "path": str(package_path.expanduser().absolute()),
        "package_id": manifest.get("package_id"),
        "expected_package_id": expected_id,
        "package_id_valid": package_id_valid,
        "file_checks": file_checks,
        "payload_shape_valid": payload_shape_valid,
        "source_file_policy_valid": source_file_policy_valid,
        "record_counts_valid": record_counts_valid,
        "asset_records_valid": asset_records_valid,
        "source_records_valid": source_records_valid,
        "fragment_records_valid": fragment_records_valid,
        "relation_records_valid": relation_records_valid,
        "relation_links_valid": relation_links_valid,
        "asset_source_links_valid": asset_source_links_valid,
        "evidence_links_valid": evidence_links_valid,
        "source_files_valid": source_files_valid,
        "publisher_identity_verified": False,
        "valid": valid,
    }


def _parse_jsonl(payload: bytes, *, name: str, maximum: int) -> list[dict[str, Any]]:
    if len(payload) > _MAX_ENTRY_BYTES:
        raise ValueError(f"{name} exceeds the entry limit")
    values: list[dict[str, Any]] = []
    try:
        text = payload.decode("utf-8")
    except UnicodeDecodeError as error:
        raise ValueError(f"{name} must be UTF-8") from error
    for line_number, line in enumerate(text.splitlines(), start=1):
        if not line.strip():
            continue
        if len(values) >= maximum:
            raise ValueError(f"{name} record count exceeds the bound")
        try:
            value = strict_json_loads(line)
        except (json.JSONDecodeError, ValueError) as error:
            raise ValueError(f"{name} line {line_number} is invalid JSON") from error
        if not isinstance(value, dict):
            raise ValueError(f"{name} line {line_number} must be an object")
        values.append(value)
    return values


def import_knowledge_package(
    vault: KnowledgeVault,
    path: str | Path,
    *,
    confirm_untrusted: bool,
) -> dict[str, Any]:
    if not confirm_untrusted:
        raise ValueError(
            "knowledge package import requires confirmation that every imported asset "
            "will remain quarantined and untrusted"
        )
    verification = verify_knowledge_package(path)
    if not verification["valid"]:
        raise ValueError("knowledge package failed integrity verification")
    manifest, payloads = _read_package(Path(path))
    assets = _parse_jsonl(
        payloads.get("assets.jsonl", b""),
        name="assets.jsonl",
        maximum=_MAX_RECORDS,
    )
    imported: list[str] = []
    skipped: list[dict[str, str]] = []
    for index, item in enumerate(assets, start=1):
        try:
            kind = item["kind"]
            memory_tier = item["memory_tier"]
            sensitivity = item["sensitivity"]
            if kind not in ASSET_KINDS or memory_tier not in MEMORY_TIERS:
                raise ValueError("unsupported kind or memory tier")
            if sensitivity not in SENSITIVITY_LEVELS:
                raise ValueError("unsupported sensitivity")
            origin_uri = item.get("uri")
            if not isinstance(origin_uri, str) or len(origin_uri) > 2_000:
                raise ValueError("origin URI is invalid")
            asset = vault.propose_asset(
                kind=cast(AssetKind, kind),
                memory_tier=cast(MemoryTier, memory_tier),
                title=item["title"],
                statement=item["statement"],
                semantic_key=item.get("semantic_key"),
                trust="untrusted",
                sensitivity=cast(Sensitivity, sensitivity),
                tags=(
                    *(
                        tag
                        for tag in item.get("tags", [])[:31]
                        if isinstance(tag, str)
                    ),
                    "imported-package",
                ),
                expires_at=item.get("expires_at"),
                origin_uri=origin_uri,
                quarantined=True,
            )
            imported.append(asset.asset_id)
        except (KeyError, TypeError, ValueError) as error:
            skipped.append({"record": str(index), "reason": str(error)})
    return {
        "schema_version": "deeplaw.knowledge-package-import/v1",
        "package_id": manifest["package_id"],
        "target_vault_id": vault.vault_id,
        "target_revision": vault.revision,
        "imported_asset_ids": imported,
        "skipped": skipped,
        "status": "quarantined",
        "publisher_identity_verified": False,
        "activation": "each imported asset requires explicit local human review",
    }
