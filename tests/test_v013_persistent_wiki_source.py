from __future__ import annotations

import shutil
from pathlib import Path

import pytest

from deeplaw import persistent_read_runtime
from deeplaw.knowledge_autonomy import (
    SINK_OPERATIONS,
    AutonomousKnowledgeStore,
    initialize_autonomous_core,
)
from deeplaw.knowledge_compiler import compile_source
from deeplaw.knowledge_store import KnowledgeVault, initialize_knowledge_vault
from deeplaw.persistent_read_runtime import PersistentReadRuntime, _LiveObserver
from deeplaw.read_services import SourceReadService, WikiReadService


def _vault(tmp_path: Path) -> Path:
    root = tmp_path / "vault"
    initialize_knowledge_vault(root, name="v013-persistent-wiki-source", scope="project")
    initialize_autonomous_core(root)
    return root


def _seed(root: Path, tmp_path: Path) -> None:
    source_path = tmp_path / "evidence.md"
    source_path.write_text(
        "# Evidence\n\n"
        "This source has enough governed text to create stable fragments for the runtime test.\n\n"
        "The evidence remains immutable and is checked before it is exposed to a reader.",
        encoding="utf-8",
    )
    with KnowledgeVault(root, read_only=False) as vault:
        compiled = compile_source(
            vault,
            source_path,
            source_kind="document",
            confirm_no_case_data=True,
        )
        review = vault.source_review_manifest(compiled["source"]["source_id"])
        vault.approve_source_assets(
            compiled["source"]["source_id"],
            confirm_reviewed=True,
            review_manifest_sha256=review["review_manifest_sha256"],
            reviewer_id="v013-runtime-fixture",
            review_reason="Activate exact synthetic evidence for read-runtime coverage.",
        )
    with AutonomousKnowledgeStore(root, read_only=False) as store:
        grant_id = store.enable_grant(
            writer_id="v013-persistent-wiki-source",
            operations=tuple(sorted(SINK_OPERATIONS)),
            max_mutations_per_minute=120,
        )["grant_id"]
        store.remember(
            grant_id=grant_id,
            idempotency_key="v013-persistent-wiki-source-seed",
            title="Persistent Wiki source",
            body="The page is served from the verified registry bundle.",
            kind="concept",
            operation="upsert_concept",
            confirm_no_case_data=True,
        )
        store.rebuild_derived()


def test_lifespan_snapshot_reuses_verified_source_and_wiki_indexes(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = _vault(tmp_path)
    _seed(root, tmp_path)
    runtime = PersistentReadRuntime(root)
    try:
        snapshot = runtime.snapshot
        assert snapshot.wiki is not None
        assert snapshot.source_integrity is not None
        assert any(
            ".deeplaw/derived/wiki/v3/" in str(item)
            for item in snapshot.identity.wiki
        )

        def fail_verify(*args: object, **kwargs: object) -> object:
            raise AssertionError("request reopened or re-verified the lifespan snapshot")

        monkeypatch.setattr(KnowledgeVault, "verify_integrity", fail_verify)
        monkeypatch.setattr(AutonomousKnowledgeStore, "verify", fail_verify)

        source = SourceReadService(root).execute(action="list", snapshot=snapshot)
        assert source["write_performed"] is False
        page_path = next(
            row["canonical_page_path"]
            for row in snapshot.wiki.page_registry["records"]
            if row["freshness"] in {"fresh", "unknown"}
        )
        page = WikiReadService(root).execute(
            action="page",
            wiki_path=page_path,
            snapshot=snapshot,
        )
        links = WikiReadService(root).execute(
            action="outlinks",
            wiki_path=page_path,
            snapshot=snapshot,
        )
        assert page["wiki_path"] == page_path
        assert "deprecation" not in page
        assert links["index_used"] is True
        assert "cursor" in links and "truncation_reason" in links
    finally:
        runtime.close()


def test_live_observer_secures_sqlite_read_sidecars(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = _vault(tmp_path)
    observed: list[Path] = []
    monkeypatch.setattr(
        persistent_read_runtime,
        "_harden_windows_sqlite_read_sidecars",
        lambda database: observed.append(database),
    )

    observer = _LiveObserver(root)
    observer.close()

    assert observed == [root / ".deeplaw" / "ledger.sqlite3"]


def test_missing_rebuildable_derived_directory_uses_canonical_snapshot(
    tmp_path: Path,
) -> None:
    root = _vault(tmp_path)
    _seed(root, tmp_path)
    shutil.rmtree(root / ".deeplaw" / "derived")

    runtime = PersistentReadRuntime(root)
    try:
        assert runtime.snapshot.wiki is None
        assert runtime.snapshot.legacy_integrity["valid"] is True
        assert runtime.snapshot.autonomous_integrity["valid"] is True
        assert runtime.snapshot.identity.manifest == ("missing",)
    finally:
        runtime.close()


def test_missing_derived_fallback_does_not_accept_a_symlink_parent(tmp_path: Path) -> None:
    root = _vault(tmp_path)
    _seed(root, tmp_path)
    derived = root / ".deeplaw" / "derived"
    shutil.rmtree(derived)
    outside = tmp_path / "outside-derived"
    outside.mkdir()
    derived.symlink_to(outside, target_is_directory=True)

    with pytest.raises(RuntimeError, match="derived manifest parent is not a safe directory"):
        PersistentReadRuntime(root)


def test_wiki_bundle_tamper_invalidates_before_serving_old_snapshot(tmp_path: Path) -> None:
    root = _vault(tmp_path)
    _seed(root, tmp_path)
    runtime = PersistentReadRuntime(root)
    old = runtime.snapshot
    try:
        top = root / ".deeplaw" / "derived" / "wiki" / "v3" / "manifest.json"
        top.write_bytes(top.read_bytes() + b"\n")
        replacement = runtime.get_snapshot()
        assert replacement is not old
        assert old.closed is True
    finally:
        runtime.close()


def test_warm_observer_identity_is_bounded_by_manifest_components(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = _vault(tmp_path)
    _seed(root, tmp_path)
    runtime = PersistentReadRuntime(root)
    calls = 0
    original = _LiveObserver._regular_file_identity

    def counted(path: Path, label: str) -> tuple[int, int, int, int, int]:
        nonlocal calls
        calls += 1
        return original(path, label)

    monkeypatch.setattr(_LiveObserver, "_regular_file_identity", staticmethod(counted))
    try:
        runtime.get_snapshot()
        assert calls <= 16
    finally:
        runtime.close()


def test_component_manifest_change_invalidates_old_snapshot(tmp_path: Path) -> None:
    root = _vault(tmp_path)
    _seed(root, tmp_path)
    runtime = PersistentReadRuntime(root)
    old = runtime.snapshot
    try:
        component = old.wiki.v3_manifest["components"][0]
        path = root / component["manifest_path"]
        path.write_bytes(path.read_bytes() + b"\n")
        with pytest.raises(RuntimeError):
            runtime.get_snapshot()
        assert old.closed is True
    finally:
        runtime.close()


def test_source_file_change_fails_exact_read_through_old_snapshot(tmp_path: Path) -> None:
    root = _vault(tmp_path)
    _seed(root, tmp_path)
    runtime = PersistentReadRuntime(root)
    try:
        source = runtime.snapshot.legacy.all_sources()[0]
        source_path = runtime.snapshot.legacy.source_file_path(source["source_id"])
        original = source_path.read_bytes()
        source_path.write_bytes(original[:-1] + bytes([original[-1] ^ 1]))
        with pytest.raises(RuntimeError, match="source bytes failed"):
            SourceReadService(root).execute(
                action="get",
                source_id=source["source_id"],
                snapshot=runtime.snapshot,
            )
    finally:
        runtime.close()


def test_no_snapshot_v3_backlinks_use_index_without_filesystem_scan(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = _vault(tmp_path)
    _seed(root, tmp_path)
    runtime = PersistentReadRuntime(root)
    try:
        page_path = next(
            row["canonical_page_path"]
            for row in runtime.snapshot.wiki.page_registry["records"]
            if row["freshness"] in {"fresh", "unknown"}
        )
    finally:
        runtime.close()

    def fail_scan(*args: object, **kwargs: object) -> object:
        raise AssertionError("v3 backlink reads must not scan the filesystem")

    monkeypatch.setattr(Path, "rglob", fail_scan)
    result = WikiReadService(root).execute(action="backlinks", wiki_path=page_path)
    assert result["index_used"] is True
    assert "deprecation" not in result
