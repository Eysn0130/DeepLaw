from __future__ import annotations

import json
from pathlib import Path

import pytest
from jsonschema import Draft202012Validator, FormatChecker

from deeplaw.knowledge_autonomy import (
    SINK_OPERATIONS,
    AutonomousKnowledgeStore,
    initialize_autonomous_core,
)
from deeplaw.knowledge_store import initialize_knowledge_vault
from deeplaw.projection import rebuild_living_wiki


def _vault(tmp_path: Path) -> Path:
    root = tmp_path / "vault"
    initialize_knowledge_vault(root, name="v013-incremental", scope="project")
    initialize_autonomous_core(root)
    return root


def _grant(store: AutonomousKnowledgeStore) -> str:
    return store.enable_grant(
        writer_id="v013-incremental-tests",
        operations=tuple(sorted(SINK_OPERATIONS)),
        max_mutations_per_minute=120,
    )["grant_id"]


def _seed(root: Path) -> None:
    with AutonomousKnowledgeStore(root, read_only=False) as store:
        grant_id = _grant(store)
        store.remember(
            grant_id=grant_id,
            idempotency_key="incremental-seed",
            title="Incremental seed",
            body="A deterministic projection fixture.",
            kind="concept",
            operation="upsert_concept",
            confirm_no_case_data=True,
        )
        store.rebuild_derived()


def test_dry_run_is_pure_and_matches_apply_change_set(tmp_path: Path) -> None:
    root = _vault(tmp_path)
    _seed(root)
    live_files = {
        path.relative_to(root).as_posix(): path.stat().st_mtime_ns
        for base in (root / "wiki", root / "canvas")
        for path in base.rglob("*")
        if path.is_file()
    }
    manifest = root / ".deeplaw/derived/tree/living-wiki-manifest.json"
    manifest_mtime = manifest.stat().st_mtime_ns
    with AutonomousKnowledgeStore(root, read_only=False) as store:
        dry = rebuild_living_wiki(store, dry_run=True)
        assert manifest.stat().st_mtime_ns == manifest_mtime
        applied = rebuild_living_wiki(store)
    assert dry["dry_run"] is True
    assert applied["dry_run"] is False
    assert dry["change_set"] == applied["change_set"]
    assert not list((root / ".deeplaw/derived/tree").glob("*journal*"))
    assert live_files == {
        path.relative_to(root).as_posix(): path.stat().st_mtime_ns
        for base in (root / "wiki", root / "canvas")
        for path in base.rglob("*")
        if path.is_file()
    }


def test_unchanged_hashes_do_not_rewrite_live_pages(tmp_path: Path) -> None:
    root = _vault(tmp_path)
    _seed(root)
    page = root / "wiki" / "overview.md"
    before = page.stat().st_mtime_ns
    with AutonomousKnowledgeStore(root, read_only=False) as store:
        result = store.rebuild_derived()
    assert page.stat().st_mtime_ns == before
    assert result["living_wiki"]["change_set"]["updated"] == []
    assert result["living_wiki"]["change_set"]["deleted"] == []


def test_change_set_schema_hash_sort_and_unique(tmp_path: Path) -> None:
    root = _vault(tmp_path)
    _seed(root)
    with AutonomousKnowledgeStore(root, read_only=False) as store:
        result = store.rebuild_derived()
    change_set = result["living_wiki"]["change_set"]
    schema = json.loads(
        (
            Path(__file__).parents[1]
            / "contracts"
                / "living-wiki-change-set.v2.schema.json"
        ).read_text()
    )
    Draft202012Validator(schema, format_checker=FormatChecker()).validate(change_set)
    for key in ("created", "updated", "deleted", "unchanged"):
        paths = [item["path"] for item in change_set[key]]
        assert paths == sorted(paths)
        assert len(paths) == len(set(paths))
    body = {key: value for key, value in change_set.items() if key != "change_set_sha256"}
    from deeplaw.util import canonical_json, sha256_bytes

    assert change_set["change_set_sha256"] == sha256_bytes(
        canonical_json(body).encode("utf-8")
    )


def test_modified_owned_file_fails_closed(tmp_path: Path) -> None:
    root = _vault(tmp_path)
    _seed(root)
    page = root / "wiki" / "overview.md"
    page.write_text(page.read_text(encoding="utf-8") + "owner edit\n", encoding="utf-8")
    with AutonomousKnowledgeStore(root, read_only=False) as store, pytest.raises(
        RuntimeError, match=r"hash/size|owned"
    ):
        store.rebuild_derived()
    assert "owner edit" in page.read_text(encoding="utf-8")
