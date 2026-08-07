from __future__ import annotations

from collections import Counter
from pathlib import Path

from deeplaw import knowledge_autonomy
from deeplaw.knowledge_autonomy import (
    SINK_OPERATIONS,
    AutonomousKnowledgeStore,
    initialize_autonomous_core,
)
from deeplaw.knowledge_store import initialize_knowledge_vault
from deeplaw.projection import builder
from deeplaw.read_services import WikiReadService


def _vault(tmp_path: Path) -> Path:
    root = tmp_path / "vault"
    initialize_knowledge_vault(root, name="v013-regressions", scope="project")
    initialize_autonomous_core(root)
    return root


def _grant(store: AutonomousKnowledgeStore) -> str:
    return store.enable_grant(
        writer_id="v013-regression",
        operations=tuple(sorted(SINK_OPERATIONS)),
        max_mutations_per_minute=120,
    )["grant_id"]


def test_default_rebuild_writes_each_derived_path_once(
    tmp_path: Path,
    monkeypatch,
) -> None:
    root = _vault(tmp_path)
    with AutonomousKnowledgeStore(root, read_only=False) as store:
        grant_id = _grant(store)
        store.remember(
            grant_id=grant_id,
            idempotency_key="projection-path-ownership",
            title="Projection path ownership",
            body="A synthetic object exercises the default Living Wiki rebuild.",
            semantic_key="v013.projection.path.ownership",
            confirm_no_case_data=True,
        )

        writes: Counter[str] = Counter()
        original_write = knowledge_autonomy._atomic_owner_write

        def record_write(path: Path, payload: bytes) -> None:
            relative = path.relative_to(root).as_posix()
            if relative.startswith(("wiki/", "canvas/")):
                writes[relative] += 1
            original_write(path, payload)

        monkeypatch.setattr(knowledge_autonomy, "_atomic_owner_write", record_write)
        monkeypatch.setattr(builder, "_atomic_owner_write", record_write)

        store.rebuild_derived()

    duplicate_paths = sorted(path for path, count in writes.items() if count > 1)
    assert duplicate_paths == []


def test_backlinks_cover_all_pages_beyond_the_filesystem_scan_cap(tmp_path: Path) -> None:
    root = _vault(tmp_path)
    wiki_root = root / "wiki"
    target = wiki_root / "zz-target.md"
    target.write_text("---\nschema: test\n---\n\n# Target\n", encoding="utf-8")

    backlink_count = 1_001
    for index in range(backlink_count):
        page = wiki_root / "aa" / f"page-{index:04d}.md"
        page.parent.mkdir(parents=True, exist_ok=True)
        page.write_text(
            f"---\nschema: test\n---\n\n[[wiki/zz-target|Target {index}]]\n",
            encoding="utf-8",
        )

    result = WikiReadService(root).execute(
        action="backlinks",
        wiki_path="wiki/zz-target.md",
        limit=20,
    )

    assert result.get("total_count") == backlink_count


def test_default_rebuild_of_363_objects_has_no_per_object_canvas(tmp_path: Path) -> None:
    root = _vault(tmp_path)
    object_count = 363
    with AutonomousKnowledgeStore(root, read_only=False) as store:
        # The sink deliberately caps each Grant at 120 mutations per minute;
        # rotate synthetic Grants so setup reaches the scale fixture without
        # bypassing that production boundary.
        grant_ids = [_grant(store) for _ in range(5)]
        for index in range(object_count):
            store.remember(
                grant_id=grant_ids[index // 90],
                idempotency_key=f"per-object-canvas-{index:03d}",
                title=f"Canvas regression object {index:03d}",
                body=f"Synthetic object {index:03d} for the standard rebuild profile.",
                semantic_key=f"v013.canvas.object.{index:03d}",
                confirm_no_case_data=True,
            )

        rebuilt = store.rebuild_derived()

    assert rebuilt["living_wiki"]["knowledge_count"] == object_count
    assert sorted((root / "canvas").glob("object-*.canvas")) == []
