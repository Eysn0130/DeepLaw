from __future__ import annotations

import json
from pathlib import Path

import pytest

from deeplaw.knowledge_autonomy import (
    SINK_OPERATIONS,
    AutonomousKnowledgeStore,
    initialize_autonomous_core,
)
from deeplaw.knowledge_compiler import compile_source
from deeplaw.knowledge_store import KnowledgeVault, initialize_knowledge_vault
from deeplaw.projection import rebuild_living_wiki
from deeplaw.projection.incremental import read_previous_v3
from deeplaw.util import canonical_json, sha256_bytes
from deeplaw.wiki import load_link_index, load_page_registry, load_resolver


def _vault(tmp_path: Path) -> Path:
    root = tmp_path / "vault"
    initialize_knowledge_vault(root, name="v013-v3-integration", scope="project")
    initialize_autonomous_core(root)
    return root


def _seed(store: AutonomousKnowledgeStore) -> None:
    grant_id = store.enable_grant(
        writer_id="v013-v3-integration-tests",
        operations=tuple(sorted(SINK_OPERATIONS)),
        max_mutations_per_minute=120,
    )["grant_id"]
    store.remember(
        grant_id=grant_id,
        idempotency_key="v3-integration-seed",
        title="V3 integration seed",
        body="A stable Living Wiki v3 projection fixture.",
        kind="concept",
        operation="upsert_concept",
        confirm_no_case_data=True,
    )


def _v3_manifest(root: Path) -> dict:
    return json.loads(
        (root / ".deeplaw" / "derived" / "wiki" / "v3" / "manifest.json").read_text(
            encoding="utf-8"
        )
    )


def test_single_projection_owns_v2_and_transitive_v3_bundle(tmp_path: Path) -> None:
    root = _vault(tmp_path)
    with AutonomousKnowledgeStore(root, read_only=False) as store:
        _seed(store)
        result = rebuild_living_wiki(store)
    v2 = json.loads(
        (root / ".deeplaw" / "derived" / "tree" / "living-wiki-manifest.json").read_text(
            encoding="utf-8"
        )
    )
    v3 = _v3_manifest(root)
    assert result["v3_manifest_sha256"] == v3["manifest_sha256"]
    assert any(
        item["path"].startswith(".deeplaw/derived/wiki/v3/")
        for item in result["change_set"]["created"]
    )
    assert not any(item["path"].startswith(".deeplaw/derived/wiki/v3/") for item in v2["files"])
    registry = load_page_registry(root, v3)
    links = load_link_index(root, v3, registry)
    resolver = load_resolver(root, v3, registry)
    assert registry["component"]["page_count"] == len(registry["records"])
    assert links["component"]["coverage_record_count"] == registry["component"]["page_count"]
    assert resolver.registry_sha256 == registry["registry_sha256"]


def test_dry_run_v3_change_set_matches_apply_without_live_mtime_change(tmp_path: Path) -> None:
    root = _vault(tmp_path)
    with AutonomousKnowledgeStore(root, read_only=False) as store:
        _seed(store)
        rebuild_living_wiki(store)
        v3_path = root / ".deeplaw" / "derived" / "wiki" / "v3" / "manifest.json"
        before = v3_path.stat().st_mtime_ns
        dry = rebuild_living_wiki(store, dry_run=True)
        assert v3_path.stat().st_mtime_ns == before
        applied = rebuild_living_wiki(store)
    assert dry["change_set"] == applied["change_set"]
    assert dry["v3_manifest_sha256"] == applied["v3_manifest_sha256"]


def test_tampered_v3_shard_fails_closed_before_new_activation(tmp_path: Path) -> None:
    root = _vault(tmp_path)
    with AutonomousKnowledgeStore(root, read_only=False) as store:
        _seed(store)
        rebuild_living_wiki(store)
        v3 = _v3_manifest(root)
        registry_component = next(
            row for row in v3["components"] if row["component"] == "page_registry"
        )
        registry_manifest = json.loads(
            (root / registry_component["manifest_path"]).read_text(encoding="utf-8")
        )
        shard = root / registry_manifest["shards"][0]["path"]
        shard.write_bytes(shard.read_bytes() + b"tampered")
        with pytest.raises(RuntimeError):
            rebuild_living_wiki(store)
        with pytest.raises(RuntimeError):
            read_previous_v3(root)


def test_mixed_v2_v3_pair_is_rejected_fail_closed(tmp_path: Path) -> None:
    root = _vault(tmp_path)
    with AutonomousKnowledgeStore(root, read_only=False) as store:
        _seed(store)
        rebuild_living_wiki(store)
        path = root / ".deeplaw" / "derived" / "tree" / "living-wiki-manifest.json"
        value = json.loads(path.read_text(encoding="utf-8"))
        value["generated_at"] = "2026-08-08T00:00:01Z"
        value["manifest_sha256"] = sha256_bytes(
            canonical_json(
                {key: item for key, item in value.items() if key != "manifest_sha256"}
            ).encode("utf-8")
        )
        path.write_text(json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n")
        with pytest.raises(RuntimeError):
            rebuild_living_wiki(store)


def test_real_source_fragment_shard_contains_unique_registered_anchor(tmp_path: Path) -> None:
    root = _vault(tmp_path)
    source = tmp_path / "guide.md"
    source.write_text(
        "# Evidence guide\n\n"
        "This source contains enough governed text to create stable fragments for a projection.\n\n"
        "## Boundary\n\n"
        "The compiled fragment remains evidence and the summary remains derived knowledge.",
        encoding="utf-8",
    )
    with KnowledgeVault(root, read_only=False) as vault:
        compiled = compile_source(
            vault,
            source,
            source_kind="document",
            confirm_no_case_data=True,
        )
    with AutonomousKnowledgeStore(root, read_only=False) as store:
        rebuild_living_wiki(store)
    snapshot = read_previous_v3(root)
    assert snapshot is not None
    resolver = load_resolver(root, snapshot["manifest"], snapshot["components"]["page_registry"])
    anchors = [
        (record, anchor)
        for record in snapshot["components"]["page_registry"]["records"]
        for anchor in record.get("anchors", [])
        if anchor["source_fragment"]["source_revision_id"]
        == compiled["identity"]["source_revision_id"]
    ]
    assert anchors
    record, anchor = anchors[0]
    page = root / record["canonical_page_path"]
    assert page.read_text(encoding="utf-8").count(f'id="{anchor["anchor"]}"') == 1
    fragment = anchor["source_fragment"]
    result = resolver.resolve({"source_fragment": fragment})
    assert result["status"] == "resolved"
    assert result["candidates"][0]["page_id"] == record["page_id"]
    assert result["candidates"][0]["anchor"]["anchor"] == anchor["anchor"]


def test_v3_without_authoritative_v2_manifest_is_rejected(tmp_path: Path) -> None:
    root = _vault(tmp_path)
    with AutonomousKnowledgeStore(root, read_only=False) as store:
        _seed(store)
        rebuild_living_wiki(store)
        (root / ".deeplaw" / "derived" / "tree" / "living-wiki-manifest.json").unlink()
        with pytest.raises(RuntimeError):
            rebuild_living_wiki(store)


def test_aggregate_page_ids_survive_unrelated_knowledge_audit_changes(tmp_path: Path) -> None:
    root = _vault(tmp_path)
    with AutonomousKnowledgeStore(root, read_only=False) as store:
        _seed(store)
        rebuild_living_wiki(store)
        first = read_previous_v3(root)
        assert first is not None
        first_ids = {
            record["canonical_page_path"]: record["page_id"]
            for record in first["components"]["page_registry"]["records"]
            if record["namespace"] == "aggregate"
        }
        grant_id = _grant_for_integration(store)
        store.remember(
            grant_id=grant_id,
            idempotency_key="v3-unrelated-change",
            title="Unrelated projection object",
            body="This audit event should not change aggregate page identities.",
            kind="memory",
            operation="remember",
            confirm_no_case_data=True,
        )
        rebuild_living_wiki(store)
        second = read_previous_v3(root)
        assert second is not None
        second_ids = {
            record["canonical_page_path"]: record["page_id"]
            for record in second["components"]["page_registry"]["records"]
            if record["namespace"] == "aggregate"
        }
    assert first_ids.items() <= second_ids.items()


def _grant_for_integration(store: AutonomousKnowledgeStore) -> str:
    return store.enable_grant(
        writer_id="v013-v3-integration-extra",
        operations=tuple(sorted(SINK_OPERATIONS)),
        max_mutations_per_minute=120,
    )["grant_id"]
