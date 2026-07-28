from __future__ import annotations

import difflib
import json
import re
from pathlib import Path, PurePosixPath
from typing import Any

from .knowledge_markdown import KNOWLEDGE_MARKDOWN_SCHEMA
from .knowledge_store import KnowledgeVault
from .util import sha256_file, strict_json_loads

PROJECTION_DIFF_SCHEMA = "deeplaw.projection-diff/v1"
_MAX_MANIFEST_BYTES = 64 * 1024 * 1024
_MAX_PAGE_BYTES = 2 * 1024 * 1024
_MAX_FILES = 300_000
_ASSET_CATEGORIES = {
    "knowledge",
    "concepts",
    "decisions",
    "constraints",
    "procedures",
    "experiences",
    "questions",
}
_ASSET_ID = re.compile(r"asset_[0-9a-f]{24}")
_KNOWLEDGE_KEY = re.compile(r"knowledge_[0-9a-f]{24}")
_FENCE = re.compile(r"(?m)^(`{4,})text\n")


def _safe_relative(value: Any) -> PurePosixPath | None:
    if not isinstance(value, str):
        return None
    path = PurePosixPath(value)
    if (
        not value
        or value.startswith("/")
        or "\\" in value
        or ".." in path.parts
        or path.as_posix() != value
    ):
        return None
    return path


def _load_projection_manifest(root: Path) -> dict[str, Any]:
    path = root / "manifest.json"
    if (
        root.is_symlink()
        or not root.is_dir()
        or path.is_symlink()
        or not path.is_file()
        or not 1 <= path.stat().st_size <= _MAX_MANIFEST_BYTES
    ):
        raise RuntimeError("projection manifest is missing or unsafe")
    value = strict_json_loads(path.read_bytes())
    required = {
        "schema_version",
        "vault_id",
        "vault_revision",
        "audit_head",
        "max_sensitivity",
        "asset_count",
        "source_count",
        "concept_count",
        "projection_file_count",
        "files",
    }
    if (
        not isinstance(value, dict)
        or set(value) != required
        or value.get("schema_version") != KNOWLEDGE_MARKDOWN_SCHEMA
        or not isinstance(value.get("files"), list)
        or len(value["files"]) > _MAX_FILES
        or value.get("projection_file_count") != len(value["files"])
    ):
        raise RuntimeError("projection manifest does not match the v2 contract")
    seen = {"manifest.json"}
    for item in value["files"]:
        relative = _safe_relative(item.get("path") if isinstance(item, dict) else None)
        if (
            not isinstance(item, dict)
            or set(item) != {"path", "byte_size", "sha256"}
            or relative is None
            or relative.as_posix() in seen
            or isinstance(item.get("byte_size"), bool)
            or not isinstance(item.get("byte_size"), int)
            or item["byte_size"] < 0
            or not isinstance(item.get("sha256"), str)
            or not re.fullmatch(r"[0-9a-f]{64}", item["sha256"])
        ):
            raise RuntimeError("projection file inventory is invalid")
        seen.add(relative.as_posix())
    return value


def _asset_page_path(path: PurePosixPath) -> bool:
    return bool(
        len(path.parts) == 2
        and path.parts[0] in _ASSET_CATEGORIES
        and path.suffix == ".md"
        and _KNOWLEDGE_KEY.fullmatch(path.stem)
    )


def _parse_asset_page(path: Path) -> dict[str, str]:
    if path.is_symlink() or not path.is_file() or path.stat().st_size > _MAX_PAGE_BYTES:
        raise RuntimeError("edited projection asset page is missing or unsafe")
    text = path.read_text(encoding="utf-8")
    if not text.startswith("---\n"):
        raise ValueError("edited projection asset page has no frontmatter")
    boundary = text.find("\n---\n", 4, 64 * 1024)
    if boundary < 0:
        raise ValueError("edited projection asset frontmatter is not bounded")
    properties: dict[str, Any] = {}
    for line in text[4:boundary].splitlines():
        if ": " not in line:
            continue
        key, raw = line.split(": ", 1)
        if key not in {"asset_id", "knowledge_key", "title", "kind", "memory_tier"}:
            continue
        try:
            properties[key] = json.loads(raw)
        except json.JSONDecodeError as error:
            raise ValueError(f"edited projection property is invalid: {key}") from error
    if (
        set(properties)
        != {
            "asset_id",
            "knowledge_key",
            "title",
            "kind",
            "memory_tier",
        }
        or not isinstance(properties["asset_id"], str)
        or not _ASSET_ID.fullmatch(properties["asset_id"])
        or not isinstance(properties["knowledge_key"], str)
        or not _KNOWLEDGE_KEY.fullmatch(properties["knowledge_key"])
        or not isinstance(properties["title"], str)
        or not 1 <= len(properties["title"]) <= 500
        or not isinstance(properties["kind"], str)
        or not isinstance(properties["memory_tier"], str)
    ):
        raise ValueError("edited projection asset properties are incomplete or invalid")
    body = text[boundary + 5 :]
    match = _FENCE.search(body)
    if match is None:
        raise ValueError("edited projection asset has no literal statement block")
    fence = match.group(1)
    statement_start = match.end()
    closing = body.find(f"\n{fence}\n", statement_start)
    if closing < 0:
        raise ValueError("edited projection literal statement block is not closed")
    statement = body[statement_start:closing]
    if not 1 <= len(statement) <= 20_000 or statement != statement.strip():
        raise ValueError("edited projection statement is not a canonical bounded string")
    return {
        "asset_id": properties["asset_id"],
        "knowledge_key": properties["knowledge_key"],
        "title": properties["title"],
        "kind": properties["kind"],
        "memory_tier": properties["memory_tier"],
        "statement": statement,
    }


def _asset_identity(vault: KnowledgeVault, asset_id: str) -> dict[str, str] | None:
    if not vault.identity_v2_enabled:
        return None
    row = vault.connection.execute(
        """
        SELECT revisions.knowledge_key, bindings.asset_revision_id
        FROM asset_revision_bindings_v2 AS bindings
        JOIN knowledge_revisions_v2 AS revisions USING(asset_revision_id)
        WHERE bindings.legacy_asset_id = ?
        """,
        (asset_id,),
    ).fetchone()
    return dict(row) if row is not None else None


def projection_diff(
    vault: KnowledgeVault,
    projection: str | Path,
) -> dict[str, Any]:
    root = Path(projection).expanduser().absolute()
    manifest = _load_projection_manifest(root)
    if manifest["vault_id"] != vault.vault_id:
        raise RuntimeError("projection belongs to a different knowledge vault")
    inventory = {item["path"]: item for item in manifest["files"]}
    modified: list[str] = []
    deleted: list[str] = []
    for relative, item in inventory.items():
        pure = PurePosixPath(relative)
        path = root.joinpath(*pure.parts)
        if path.is_symlink() or not path.is_file():
            deleted.append(relative)
        elif path.stat().st_size != item["byte_size"] or sha256_file(path) != item["sha256"]:
            modified.append(relative)
    actual = {
        child.relative_to(root).as_posix()
        for child in root.rglob("*")
        if child.is_file() and not child.is_symlink()
    }
    added = sorted(actual - set(inventory) - {"manifest.json"})
    changes: list[dict[str, Any]] = []
    for relative in sorted(modified):
        pure = PurePosixPath(relative)
        change: dict[str, Any] = {
            "path": relative,
            "change": "modified",
            "projection_kind": "derived",
            "proposal_eligible": False,
            "errors": [],
        }
        if not _asset_page_path(pure):
            changes.append(change)
            continue
        try:
            edited = _parse_asset_page(root.joinpath(*pure.parts))
            asset = vault.get_asset(edited["asset_id"])
            identity = _asset_identity(vault, asset.asset_id)
            if identity is None:
                raise RuntimeError("edited asset has no Identity v2 binding")
            if (
                identity["knowledge_key"] != edited["knowledge_key"]
                or pure.stem != edited["knowledge_key"]
                or asset.kind != edited["kind"]
                or asset.memory_tier != edited["memory_tier"]
            ):
                raise ValueError("edited projection identity or immutable type changed")
            changed_fields = [
                field for field in ("title", "statement") if getattr(asset, field) != edited[field]
            ]
            before = [asset.title, *asset.statement.splitlines()]
            after = [edited["title"], *edited["statement"].splitlines()]
            field_diff = list(
                difflib.unified_diff(
                    before,
                    after,
                    fromfile=f"canonical:{asset.asset_id}",
                    tofile=f"projection:{relative}",
                    lineterm="",
                    n=3,
                )
            )
            change.update(
                {
                    "projection_kind": "knowledge_asset",
                    "asset_id": asset.asset_id,
                    "knowledge_key": identity["knowledge_key"],
                    "asset_revision_id": identity["asset_revision_id"],
                    "changed_fields": changed_fields,
                    "edited_title": edited["title"],
                    "edited_statement": edited["statement"],
                    "diff": field_diff[:500],
                    "diff_truncated": len(field_diff) > 500,
                    "proposal_eligible": bool(changed_fields),
                }
            )
        except (KeyError, OSError, RuntimeError, UnicodeDecodeError, ValueError) as error:
            change["errors"].append(str(error))
        changes.append(change)
    for relative in sorted(deleted):
        changes.append(
            {
                "path": relative,
                "change": "deleted",
                "projection_kind": "derived",
                "proposal_eligible": False,
                "errors": [],
            }
        )
    for relative in added:
        changes.append(
            {
                "path": relative,
                "change": "added",
                "projection_kind": "untracked",
                "proposal_eligible": False,
                "errors": [],
            }
        )
    eligible = [item for item in changes if item["proposal_eligible"]]
    invalid = [item for item in changes if item["errors"]]
    stale = bool(
        manifest["vault_revision"] != vault.revision or manifest["audit_head"] != vault.audit_head
    )
    return {
        "schema_version": PROJECTION_DIFF_SCHEMA,
        "vault_id": vault.vault_id,
        "projection": str(root),
        "projection_revision": manifest["vault_revision"],
        "projection_audit_head": manifest["audit_head"],
        "current_revision": vault.revision,
        "current_audit_head": vault.audit_head,
        "stale": stale,
        "modified_count": len(modified),
        "added_count": len(added),
        "deleted_count": len(deleted),
        "proposal_eligible_count": len(eligible),
        "invalid_change_count": len(invalid),
        "changes": changes[:2000],
        "changes_truncated": len(changes) > 2000,
        "canonical_write_performed": False,
    }


def propose_projection_edits(
    vault: KnowledgeVault,
    projection: str | Path,
    *,
    confirm_no_case_data: bool,
) -> dict[str, Any]:
    if vault.read_only:
        raise RuntimeError("projection proposals require a writable knowledge vault")
    if not confirm_no_case_data:
        raise ValueError("projection proposals require --confirm-no-case-data")
    diff = projection_diff(vault, projection)
    if diff["stale"]:
        raise RuntimeError("projection is stale; export the current vault and reapply the edit")
    if diff["invalid_change_count"]:
        raise RuntimeError("projection contains invalid edited asset pages")
    root = Path(projection).expanduser().absolute()
    projection_manifest_sha256 = sha256_file(root / "manifest.json")
    proposals: list[dict[str, Any]] = []
    for change in diff["changes"]:
        if not change["proposal_eligible"]:
            continue
        if not vault.verify_asset(change["asset_id"])["valid"]:
            raise RuntimeError("projection predecessor failed source verification")
        origin_uri = (
            f"deeplaw-projection://{vault.vault_id}/{projection_manifest_sha256}/{change['path']}"
        )
        proposal = vault.propose_asset_revision(
            change["asset_id"],
            title=change["edited_title"],
            statement=change["edited_statement"],
            origin_uri=origin_uri,
        )
        proposals.append(proposal.to_dict())
    return {
        "schema_version": "deeplaw.projection-proposal-result/v1",
        "vault_id": vault.vault_id,
        "projection": str(root),
        "projection_manifest_sha256": projection_manifest_sha256,
        "proposal_count": len(proposals),
        "proposals": proposals,
        "review_required": True,
        "approval_inherited": False,
        "canonical_active_knowledge_modified": False,
    }
