from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import sqlite3
from pathlib import Path
from typing import Any

from deeplaw.knowledge_autonomy import AutonomousKnowledgeStore
from deeplaw.util import canonical_json, sha256_bytes

_OPEN_REVIEW_ROOTS = ("knowledge", "wiki", "canvas")
_CONTROL_FILES = ("vault.json", ".deeplaw/manifest.json")
_FORBIDDEN_PARTS = {"capabilities", "snapshots", "staging", "update"}
_FORBIDDEN_SUFFIXES = {".token", ".key", ".pem"}


def _regular_file(path: Path) -> None:
    if path.is_symlink() or not path.is_file():
        raise RuntimeError(f"semantic review input is missing or unsafe: {path.name}")


def _copy_regular_file(source: Path, destination: Path) -> None:
    _regular_file(source)
    destination.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    shutil.copyfile(source, destination)
    if os.name != "nt":
        os.chmod(destination, 0o600)


def _copy_open_tree(source: Path, destination: Path) -> None:
    if source.is_symlink() or not source.is_dir():
        raise RuntimeError(f"semantic review workspace root is missing or unsafe: {source.name}")
    destination.mkdir(mode=0o700, parents=True, exist_ok=True)
    for path in sorted(source.rglob("*")):
        relative = path.relative_to(source)
        if path.is_symlink():
            raise RuntimeError(f"semantic review workspace contains a symlink: {relative}")
        target = destination / relative
        if path.is_dir():
            target.mkdir(mode=0o700, parents=True, exist_ok=True)
        elif path.is_file():
            _copy_regular_file(path, target)
        else:
            raise RuntimeError(f"semantic review workspace contains an unsafe entry: {relative}")


def _backup_ledger(source: Path, destination: Path) -> None:
    _regular_file(source)
    destination.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    source_connection = sqlite3.connect(f"{source.as_uri()}?mode=ro", uri=True)
    destination_connection = sqlite3.connect(destination)
    try:
        source_connection.execute("PRAGMA query_only = ON")
        source_connection.backup(destination_connection)
        integrity = destination_connection.execute("PRAGMA integrity_check").fetchone()
        if integrity is None or integrity[0] != "ok":
            raise RuntimeError("semantic review Ledger backup failed integrity_check")
    finally:
        destination_connection.close()
        source_connection.close()
    if os.name != "nt":
        os.chmod(destination, 0o600)


def _inventory(root: Path) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    for path in sorted(root.rglob("*")):
        if path.is_symlink():
            raise RuntimeError("semantic review bundle contains a symlink")
        if not path.is_file():
            continue
        relative = path.relative_to(root)
        if (
            _FORBIDDEN_PARTS.intersection(relative.parts)
            or path.suffix.lower() in _FORBIDDEN_SUFFIXES
        ):
            raise RuntimeError("semantic review bundle contains forbidden capability material")
        payload = path.read_bytes()
        records.append(
            {
                "path": relative.as_posix(),
                "bytes": len(payload),
                "sha256": hashlib.sha256(payload).hexdigest(),
            }
        )
    return records


def export_review_bundle(vault: Path, output: Path) -> dict[str, Any]:
    source_root = vault.expanduser().absolute()
    target_root = output.expanduser().absolute()
    if target_root.exists() or target_root.is_symlink():
        raise FileExistsError("semantic review bundle output must be new")
    if source_root.is_symlink() or not source_root.is_dir():
        raise RuntimeError("semantic review Vault is missing or unsafe")

    with AutonomousKnowledgeStore(source_root, read_only=True) as source_store:
        source_verification = source_store.verify()
        if not source_verification.get("valid"):
            raise RuntimeError("source semantic review Vault failed verification")
        source_vault_id = source_store.vault_id
        source_audit_head = source_store.audit_head

    target_root.mkdir(mode=0o700, parents=True)
    for relative in _CONTROL_FILES:
        _copy_regular_file(source_root / relative, target_root / relative)
    (target_root / "sources").mkdir(mode=0o700)
    _backup_ledger(
        source_root / ".deeplaw" / "ledger.sqlite3",
        target_root / ".deeplaw" / "ledger.sqlite3",
    )
    for relative in _OPEN_REVIEW_ROOTS:
        _copy_open_tree(source_root / relative, target_root / relative)

    with AutonomousKnowledgeStore(target_root, read_only=True) as store:
        vault_id = store.vault_id
        audit_head = store.audit_head
        if vault_id != source_vault_id or audit_head != source_audit_head:
            raise RuntimeError("sanitized semantic review Ledger changed identity or audit head")

    files = _inventory(target_root)
    manifest = {
        "schema_version": "deeplaw.semantic-review-bundle/v1",
        "vault_id": vault_id,
        "audit_head": audit_head,
        "content_policy": "isolated_public_semantic_fixture_only",
        "full_vault_recovery": False,
        "scoring_ledger_integrity_checked": True,
        "source_vault_verified_before_export": True,
        "included_roots": ["knowledge", "wiki", "canvas"],
        "excluded_roots": [
            ".deeplaw/capabilities",
            ".deeplaw/objects",
            ".deeplaw/snapshots",
            ".deeplaw/staging",
            ".deeplaw/update",
            "sources",
        ],
        "capability_tokens_included": False,
        "files": files,
    }
    manifest["inventory_sha256"] = sha256_bytes(canonical_json(files).encode("utf-8"))
    manifest_path = target_root / "semantic-review-bundle.json"
    manifest_path.write_text(canonical_json(manifest) + "\n", encoding="utf-8")
    if os.name != "nt":
        os.chmod(manifest_path, 0o600)
    return manifest


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Export a read-only Semantic Gold review bundle without capability material."
    )
    parser.add_argument("--vault", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    arguments = parser.parse_args()
    result = export_review_bundle(arguments.vault, arguments.output)
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
