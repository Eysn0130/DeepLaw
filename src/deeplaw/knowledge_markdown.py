from __future__ import annotations

import json
import os
import re
import secrets
import shutil
from html import escape
from pathlib import Path, PurePosixPath
from typing import Any

from .knowledge_models import SENSITIVITY_LEVELS, Sensitivity, utc_now
from .knowledge_store import KnowledgeVault
from .util import sha256_bytes, sha256_file, strict_json_loads

KNOWLEDGE_MARKDOWN_SCHEMA = "deeplaw.knowledge-markdown/v1"
_SENSITIVITY_RANK = {
    "public": 0,
    "internal": 1,
    "private": 2,
    "restricted": 3,
}
_MARKDOWN_INLINE = re.compile(r"([\\`*_[\]{}()#+.!|>\-])")


def _yaml_string(value: str) -> str:
    return json.dumps(value, ensure_ascii=False)


def _markdown_inline(value: str) -> str:
    single_line = " ".join(value.splitlines())
    return _MARKDOWN_INLINE.sub(r"\\\1", escape(single_line, quote=False))


def _literal_block(value: str) -> list[str]:
    longest = max((len(match.group(0)) for match in re.finditer(r"`+", value)), default=0)
    fence = "`" * max(3, longest + 1)
    return [f"{fence}text", value, fence]


def _write(path: Path, text: str) -> dict[str, Any]:
    payload = text.encode("utf-8")
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    path.write_bytes(payload)
    os.chmod(path, 0o600)
    return {
        "path": path.as_posix(),
        "byte_size": len(payload),
        "sha256": sha256_bytes(payload),
    }


def _safe_remove_tree(path: Path) -> None:
    if path.is_symlink() or not path.is_dir():
        raise RuntimeError("Markdown export destination is unsafe")
    for child in path.rglob("*"):
        if child.is_symlink():
            raise RuntimeError("Markdown export destination contains a symbolic link")
    shutil.rmtree(path)


def _require_owned_export(path: Path) -> None:
    manifest_path = path / "manifest.json"
    if (
        manifest_path.is_symlink()
        or not manifest_path.is_file()
        or manifest_path.stat().st_size > 4 * 1024 * 1024
    ):
        raise RuntimeError("refusing to replace a directory not owned by a DeepLaw export")
    try:
        manifest = strict_json_loads(manifest_path.read_bytes())
    except (UnicodeDecodeError, json.JSONDecodeError, ValueError) as error:
        raise RuntimeError("refusing to replace an invalid DeepLaw export") from error
    expected_fields = {
        "schema_version",
        "vault_id",
        "vault_revision",
        "audit_head",
        "max_sensitivity",
        "asset_count",
        "files",
    }
    if not isinstance(manifest, dict) or set(manifest) != expected_fields:
        raise RuntimeError("refusing to replace an invalid DeepLaw export")
    files = manifest.get("files")
    if (
        manifest.get("schema_version") != KNOWLEDGE_MARKDOWN_SCHEMA
        or not isinstance(manifest.get("vault_id"), str)
        or not manifest["vault_id"].startswith("vault_")
        or isinstance(manifest.get("vault_revision"), bool)
        or not isinstance(manifest.get("vault_revision"), int)
        or manifest["vault_revision"] < 0
        or not isinstance(manifest.get("audit_head"), str)
        or len(manifest["audit_head"]) != 64
        or manifest.get("max_sensitivity") not in SENSITIVITY_LEVELS
        or isinstance(manifest.get("asset_count"), bool)
        or not isinstance(manifest.get("asset_count"), int)
        or manifest["asset_count"] < 1
        or not isinstance(files, list)
        or len(files) < 2
    ):
        raise RuntimeError("refusing to replace a directory not owned by a DeepLaw export")
    expected_paths = {"manifest.json"}
    asset_files = 0
    for item in files:
        if not isinstance(item, dict) or set(item) != {"path", "byte_size", "sha256"}:
            raise RuntimeError("refusing to replace an invalid DeepLaw export")
        relative = item["path"]
        pure_path = PurePosixPath(relative) if isinstance(relative, str) else None
        if (
            pure_path is None
            or pure_path.is_absolute()
            or ".." in pure_path.parts
            or pure_path.as_posix() != relative
            or relative in expected_paths
            or isinstance(item["byte_size"], bool)
            or not isinstance(item["byte_size"], int)
            or item["byte_size"] < 0
            or not isinstance(item["sha256"], str)
            or len(item["sha256"]) != 64
        ):
            raise RuntimeError("refusing to replace an invalid DeepLaw export")
        target = path.joinpath(*pure_path.parts)
        if (
            target.is_symlink()
            or not target.is_file()
            or target.stat().st_size != item["byte_size"]
            or sha256_file(target) != item["sha256"]
        ):
            raise RuntimeError("refusing to replace a modified DeepLaw export")
        expected_paths.add(relative)
        if relative not in {"INDEX.md", "manifest.json"}:
            asset_files += 1
    actual_paths = {
        child.relative_to(path).as_posix()
        for child in path.rglob("*")
        if child.is_file()
    }
    if (
        actual_paths != expected_paths
        or "INDEX.md" not in expected_paths
        or asset_files != manifest["asset_count"]
    ):
        raise RuntimeError(
            "refusing to replace a DeepLaw export with untracked or missing files"
        )


def export_knowledge_markdown(
    vault: KnowledgeVault,
    output: str | Path,
    *,
    max_sensitivity: Sensitivity = "private",
    replace: bool = False,
) -> dict[str, Any]:
    if max_sensitivity not in SENSITIVITY_LEVELS:
        raise ValueError("unsupported Markdown export sensitivity")
    if not vault.verify_integrity()["valid"]:
        raise RuntimeError("knowledge vault integrity is invalid; Markdown export stopped")
    destination = Path(output).expanduser().absolute()
    if destination.is_symlink():
        raise RuntimeError("Markdown export destination must not be a symbolic link")
    if destination.exists() and not replace:
        raise FileExistsError(
            "Markdown export destination already exists; use an empty new path or --replace"
        )
    if destination.exists():
        _require_owned_export(destination)
    destination.parent.mkdir(parents=True, exist_ok=True)
    stage = destination.parent / f".{destination.name}.{secrets.token_hex(8)}.tmp"
    stage.mkdir(mode=0o700)
    allowed_rank = _SENSITIVITY_RANK[max_sensitivity]
    assets = [
        asset
        for asset in vault.all_assets(statuses=("active",))
        if _SENSITIVITY_RANK[asset.sensitivity] <= allowed_rank
        and (asset.expires_at is None or asset.expires_at > utc_now())
    ]
    source_integrity = vault.verify_source_files(
        reference.source_id
        for asset in assets
        for reference in asset.source_refs
    )
    if not source_integrity["valid"]:
        raise RuntimeError(
            "selected Knowledge Asset source evidence failed integrity verification; "
            "Markdown export stopped"
        )
    if not assets:
        _safe_remove_tree(stage)
        raise ValueError("no active assets satisfy the Markdown export sensitivity policy")
    selected_ids = {asset.asset_id for asset in assets}
    source_by_id = {source["source_id"]: source for source in vault.all_sources()}
    relations = [
        relation
        for relation in vault.all_relations()
        if relation["subject_asset_id"] in selected_ids
        and relation["object_asset_id"] in selected_ids
    ]
    for relation in relations:
        evidence_fragment_id = relation["evidence_fragment_id"]
        if evidence_fragment_id is None:
            continue
        fragment = vault.get_fragment(evidence_fragment_id)
        evidence_source = source_by_id[fragment["source_id"]]
        if _SENSITIVITY_RANK[evidence_source["sensitivity"]] > allowed_rank:
            _safe_remove_tree(stage)
            raise ValueError(
                "knowledge relation evidence exceeds the Markdown sensitivity policy"
            )
    outgoing: dict[str, list[dict[str, Any]]] = {}
    incoming: dict[str, list[dict[str, Any]]] = {}
    for relation in relations:
        outgoing.setdefault(relation["subject_asset_id"], []).append(relation)
        incoming.setdefault(relation["object_asset_id"], []).append(relation)
    files: list[dict[str, Any]] = []
    index_lines = [
        "# DeepLaw Knowledge Assets",
        "",
        f"- Vault: `{vault.vault_id}`",
        f"- Revision: `{vault.revision}`",
        f"- Audit head: `{vault.audit_head}`",
        f"- Maximum exported sensitivity: `{max_sensitivity}`",
        "",
        "This directory is a deterministic human-readable projection. "
        "The SQLite vault remains canonical.",
        "",
        "## Assets",
        "",
    ]
    for asset in assets:
        relative = Path(asset.memory_tier) / asset.kind / f"{asset.asset_id}.md"
        index_lines.append(
            f"- [{asset.asset_id}]({relative.as_posix()}) — "
            f"{_markdown_inline(asset.title)}"
        )
        source_references = [reference.to_dict() for reference in asset.source_refs]
        relation_lines: list[str] = []
        for relation in outgoing.get(asset.asset_id, []):
            relation_lines.append(
                f"- `{relation['predicate']}` → "
                f"`deeplaw://{vault.vault_id}/assets/{relation['object_asset_id']}`"
            )
        for relation in incoming.get(asset.asset_id, []):
            relation_lines.append(
                f"- ← `{relation['predicate']}` from "
                f"`deeplaw://{vault.vault_id}/assets/{relation['subject_asset_id']}`"
            )
        content = "\n".join(
            [
                "---",
                f"schema: {_yaml_string('deeplaw.knowledge-asset/v1')}",
                f"asset_id: {_yaml_string(asset.asset_id)}",
                f"uri: {_yaml_string(asset.uri)}",
                f"kind: {_yaml_string(asset.kind)}",
                f"memory_tier: {_yaml_string(asset.memory_tier)}",
                f"status: {_yaml_string(asset.status)}",
                f"verification: {_yaml_string(asset.verification)}",
                f"trust: {_yaml_string(asset.trust)}",
                f"sensitivity: {_yaml_string(asset.sensitivity)}",
                "legal_authority: false",
                f"directive_mode: {_yaml_string(asset.directive_mode)}",
                f"content_sha256: {_yaml_string(asset.content_sha256)}",
                "---",
                "",
                f"# {_markdown_inline(asset.title)}",
                "",
                *_literal_block(asset.statement),
                "",
                "## Provenance",
                "",
                "```json",
                json.dumps(
                    source_references,
                    ensure_ascii=False,
                    indent=2,
                    sort_keys=True,
                ),
                "```",
                "",
                "## Relations",
                "",
                *(relation_lines or ["- None"]),
                "",
            ]
        )
        file_info = _write(stage / relative, content)
        file_info["path"] = relative.as_posix()
        files.append(file_info)
    index_info = _write(stage / "INDEX.md", "\n".join(index_lines) + "\n")
    index_info["path"] = "INDEX.md"
    files.append(index_info)
    files.sort(key=lambda item: item["path"])
    manifest = {
        "schema_version": KNOWLEDGE_MARKDOWN_SCHEMA,
        "vault_id": vault.vault_id,
        "vault_revision": vault.revision,
        "audit_head": vault.audit_head,
        "max_sensitivity": max_sensitivity,
        "asset_count": len(assets),
        "files": files,
    }
    manifest_info = _write(
        stage / "manifest.json",
        json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
    )
    manifest_info["path"] = "manifest.json"
    if destination.exists():
        _safe_remove_tree(destination)
    os.replace(stage, destination)
    return {
        **manifest,
        "manifest": manifest_info,
        "output": str(destination),
    }
