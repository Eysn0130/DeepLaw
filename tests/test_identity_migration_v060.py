from __future__ import annotations

import hashlib
import json
import os
import sqlite3
import subprocess
import sys
from pathlib import Path

import pytest

from deeplaw.knowledge_compiler import compile_source
from deeplaw.knowledge_store import (
    KNOWLEDGE_EVENT_SCHEMA,
    KnowledgeVault,
    initialize_knowledge_vault,
    restore_knowledge_migration_backup,
    verify_knowledge_migration_backup,
)
from deeplaw.util import canonical_json, sha256_bytes, stable_id

_HISTORICAL_V060_WHEEL_SHA256 = (
    "04523ef7fef320a4a7452eb6aefeebfe1dcb745ac2a2d48edafa795a62f7ab7a"
)


def _v060_wheel() -> Path:
    supplied = os.environ.get("DEEPLAW_V060_WHEEL")
    if supplied:
        return Path(supplied).expanduser().resolve()
    return (
        Path(__file__).resolve().parents[1]
        / "dist"
        / "deeplaw-0.6.0-py3-none-any.whl"
    )


def _verified_v060_wheel() -> Path | None:
    wheel = _v060_wheel()
    if not wheel.is_file():
        return None
    expected = os.environ.get(
        "DEEPLAW_V060_WHEEL_SHA256",
        _HISTORICAL_V060_WHEEL_SHA256,
    )
    if hashlib.sha256(wheel.read_bytes()).hexdigest() != expected:
        return None
    return wheel


def _build_real_v060_vault(tmp_path: Path, wheel: Path) -> Path:
    root = tmp_path / "legacy-vault"
    source = tmp_path / "legacy.md"
    source.write_text(
        "# Legacy source\nThe v0.6 source must survive additive migration.\n",
        encoding="utf-8",
    )
    code = """
import sys
from pathlib import Path
from deeplaw.knowledge_compiler import compile_source
from deeplaw.knowledge_store import KnowledgeVault, initialize_knowledge_vault

root = Path(sys.argv[1])
source = Path(sys.argv[2])
initialize_knowledge_vault(root, name="legacy-v060", scope="project")
with KnowledgeVault(root, read_only=False) as vault:
    compiled = compile_source(
        vault,
        source,
        source_kind="document",
        confirm_no_case_data=True,
    )
    vault.approve_asset(compiled["asset_ids"][0], confirm_reviewed=True)
"""
    environment = dict(os.environ)
    environment["PYTHONPATH"] = str(wheel)
    result = subprocess.run(
        [sys.executable, "-c", code, str(root), str(source)],
        cwd=tmp_path,
        env=environment,
        capture_output=True,
        text=True,
        check=False,
        timeout=60,
    )
    assert result.returncode == 0, result.stderr
    return root


def _drop_additive_identity_projection(root: Path) -> None:
    database = root / "vault.sqlite3"
    connection = sqlite3.connect(database)
    try:
        connection.execute("PRAGMA foreign_keys = OFF")
        rows = connection.execute(
            "SELECT name FROM sqlite_master "
            "WHERE type = 'table' AND (name = 'identity_v2' OR name LIKE '%_v2')"
        ).fetchall()
        for (name,) in reversed(rows):
            if not name.replace("_", "").isalnum():
                raise AssertionError(f"unsafe fixture table name: {name}")
            connection.execute(f'DROP TABLE "{name}"')
        connection.commit()
    finally:
        connection.close()


def _downgrade_events_to_v060_contract(root: Path) -> None:
    database = root / "vault.sqlite3"
    connection = sqlite3.connect(database)
    connection.row_factory = sqlite3.Row
    try:
        retained: list[dict[str, object]] = []
        vault_id = connection.execute(
            "SELECT value FROM metadata WHERE key = 'vault_id'"
        ).fetchone()[0]
        for row in connection.execute("SELECT * FROM events ORDER BY sequence"):
            if row["event_type"] == "identity_v2_snapshot":
                continue
            payload = json.loads(row["payload_json"])
            if row["event_type"] == "source_compiled":
                fragment_ids = [
                    item["fragment_id"]
                    for item in connection.execute(
                        "SELECT fragment_id FROM source_fragments "
                        "WHERE source_id = ? ORDER BY ordinal",
                        (row["object_id"],),
                    )
                ]
                asset_ids = []
                for asset in connection.execute(
                    "SELECT asset_id, source_refs_json FROM assets "
                    "ORDER BY created_at, asset_id"
                ):
                    references = json.loads(asset["source_refs_json"])
                    if (
                        len(references) == 1
                        and references[0]["source_id"] == row["object_id"]
                    ):
                        asset_ids.append(asset["asset_id"])
                membership = [
                    {"fragment_id": fragment_id, "asset_id": asset_id}
                    for fragment_id, asset_id in zip(
                        fragment_ids, asset_ids, strict=True
                    )
                ]
                payload = {
                    "source_sha256": payload["source_sha256"],
                    "fragment_count": len(fragment_ids),
                    "asset_count": len(asset_ids),
                    "membership_sha256": sha256_bytes(
                        canonical_json(membership).encode("utf-8")
                    ),
                    "instruction_risk": payload["instruction_risk"],
                    "compiler": payload["compiler"],
                    "source_key": payload["source_key"],
                    "previous_source_id": payload["previous_source_id"],
                    "source_status": payload["source_status"],
                }
            object_id = row["object_id"]
            if row["event_type"] == "review_recorded":
                object_id = stable_id(
                    "review",
                    vault_id,
                    payload["receipt_sha256"],
                    str(len(retained)),
                )
                connection.execute(
                    "UPDATE review_receipts SET review_receipt_id = ? "
                    "WHERE review_receipt_id = ?",
                    (object_id, row["object_id"]),
                )
            retained.append(
                {
                    "event_type": row["event_type"],
                    "object_id": object_id,
                    "payload": payload,
                    "created_at": row["created_at"],
                }
            )
        connection.execute("DELETE FROM events")
        previous_hash: str | None = None
        for sequence, item in enumerate(retained):
            envelope = {
                "schema_version": KNOWLEDGE_EVENT_SCHEMA,
                "sequence": sequence,
                "event_type": item["event_type"],
                "object_id": item["object_id"],
                "payload": item["payload"],
                "previous_hash": previous_hash,
                "created_at": item["created_at"],
            }
            event_hash = sha256_bytes(canonical_json(envelope).encode("utf-8"))
            connection.execute(
                "INSERT INTO events VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    sequence,
                    KNOWLEDGE_EVENT_SCHEMA,
                    item["event_type"],
                    item["object_id"],
                    canonical_json(item["payload"]),
                    previous_hash,
                    event_hash,
                    item["created_at"],
                ),
            )
            previous_hash = event_hash
        connection.execute(
            "UPDATE metadata SET value = ? WHERE key = 'revision'",
            (str(len(retained) - 1),),
        )
        connection.execute(
            "UPDATE metadata SET value = ? WHERE key = 'audit_head'",
            (previous_hash,),
        )
        connection.commit()
    finally:
        connection.close()


def _build_v060_schema_fixture(tmp_path: Path) -> Path:
    root = tmp_path / "legacy-vault"
    source = tmp_path / "legacy.md"
    source.write_text(
        "# Legacy source\nThe v0.6 source must survive additive migration.\n",
        encoding="utf-8",
    )
    initialize_knowledge_vault(root, name="legacy-v060", scope="project")
    with KnowledgeVault(root, read_only=False) as vault:
        compiled = compile_source(
            vault,
            source,
            source_kind="document",
            confirm_no_case_data=True,
        )
        vault.approve_asset(compiled["asset_ids"][0], confirm_reviewed=True)
    _downgrade_events_to_v060_contract(root)
    _drop_additive_identity_projection(root)
    with KnowledgeVault(root, read_only=True) as vault:
        assert vault.identity_v2_enabled is False
        assert vault.verify_integrity()["valid"] is True
    return root


def _assert_migration_round_trip(root: Path, backup: Path) -> None:
    with KnowledgeVault(root, read_only=True) as vault:
        assert vault.identity_v2_enabled is False
        before_revision = vault.revision
        before_assets = [asset.asset_id for asset in vault.all_assets()]
        plan = vault.migrate_identity_v2(apply=False)
        assert plan["required"] is True

    with KnowledgeVault(root, read_only=False) as vault:
        applied = vault.migrate_identity_v2(apply=True, backup_path=backup)
        verification = vault.verify_identity_v2_migration()
        after_assets = [asset.asset_id for asset in vault.all_assets()]

    assert applied["applied"] is True
    assert applied["search_index"]["tokenizer_profile"] == "deeplaw-mixed-cjk-code/2"
    assert verification["valid"] is True
    assert after_assets == before_assets
    assert verify_knowledge_migration_backup(
        backup,
        expected_vault_id=applied["vault_id"],
    )["valid"] is True

    restored = restore_knowledge_migration_backup(root, backup=backup, confirm=True)
    assert restored["restored"] is True
    with KnowledgeVault(root, read_only=True) as vault:
        assert vault.identity_v2_enabled is False
        assert vault.revision == before_revision
        assert [asset.asset_id for asset in vault.all_assets()] == before_assets


def test_v060_schema_fixture_additive_migration_verification_and_rollback(
    tmp_path: Path,
) -> None:
    root = _build_v060_schema_fixture(tmp_path)
    _assert_migration_round_trip(root, tmp_path / "identity-v2-backup")


@pytest.mark.skipif(
    _verified_v060_wheel() is None,
    reason="exact historical v0.6 wheel unavailable",
)
def test_real_v060_wheel_additive_migration_verification_and_rollback(
    tmp_path: Path,
) -> None:
    wheel = _verified_v060_wheel()
    assert wheel is not None
    root = _build_real_v060_vault(tmp_path, wheel)
    _assert_migration_round_trip(root, tmp_path / "identity-v2-backup")
