from __future__ import annotations

import json
from pathlib import Path

import pytest
from jsonschema import Draft202012Validator, FormatChecker

import deeplaw.projection.profiles as projection_profiles
from deeplaw.compilation.coordinator import CompilationCoordinator
from deeplaw.knowledge_autonomy import SINK_OPERATIONS, AutonomousKnowledgeStore
from deeplaw.knowledge_store import initialize_knowledge_vault
from deeplaw.projection.profiles import projection_profile
from deeplaw.util import canonical_json, sha256_bytes


def _vault(tmp_path: Path) -> Path:
    root = tmp_path / "vault"
    initialize_knowledge_vault(root, name="v013-profiles", scope="project")
    from deeplaw.knowledge_autonomy import initialize_autonomous_core

    initialize_autonomous_core(root)
    return root


def _grant(store: AutonomousKnowledgeStore) -> str:
    return store.enable_grant(
        writer_id="v013-profile-tests",
        operations=tuple(sorted(SINK_OPERATIONS)),
        max_mutations_per_minute=120,
    )["grant_id"]


def _validate(root: Path, contract: str, value: dict[str, object]) -> None:
    del root
    schema = json.loads((Path(__file__).parents[1] / "contracts" / contract).read_text())
    Draft202012Validator(schema, format_checker=FormatChecker()).validate(value)


def test_default_rebuild_uses_standard_profile_and_v2_manifest(tmp_path: Path) -> None:
    root = _vault(tmp_path)
    with AutonomousKnowledgeStore(root, read_only=False) as store:
        grant_id = _grant(store)
        store.remember(
            grant_id=grant_id,
            idempotency_key="standard-profile",
            title="Standard profile object",
            body="The default projection keeps sharded indexes without advanced fan-out.",
            kind="concept",
            operation="upsert_concept",
            confirm_no_case_data=True,
        )
        result = store.rebuild_derived()

    profile = projection_profile("standard")
    assert profile["version"] == "2"
    assert profile["kind_shards"] is True
    assert profile["kind_indexes"] is True
    assert profile["communities"] is False
    assert all(
        profile[feature] is False
        for feature in (
            "global_canvas",
            "kind_canvas",
            "community_canvas",
            "per_object_canvas",
            "local_canvas_per_object",
        )
    )
    manifest_path = root / ".deeplaw" / "derived" / "tree" / "living-wiki-manifest.json"
    manifest = json.loads(manifest_path.read_text())
    assert manifest["schema_version"] == "deeplaw.living-wiki-manifest/v2"
    assert manifest["configuration"]["projection_profile"] == profile
    assert manifest["configuration"]["projection_profile_sha256"] == sha256_bytes(
        canonical_json(profile).encode("utf-8")
    )
    assert manifest["configuration"]["local_canvas_per_object"] is False
    assert not list((root / "wiki" / "communities").rglob("*.md"))
    assert not list((root / "canvas").rglob("*.canvas"))
    assert not list((root / "wiki" / "concepts").glob("knowledge_*.md"))
    registry_manifest = json.loads(
        (root / ".deeplaw/derived/wiki/v3/registry/manifest.json").read_text()
    )
    registry_records = []
    for shard in registry_manifest["shards"]:
        registry_records.extend(json.loads((root / shard["path"]).read_text())["records"])
    knowledge_record = next(
        record for record in registry_records if record.get("namespace") == "knowledge"
    )
    assert knowledge_record["canonical_page_path"].startswith("knowledge/")
    assert result["living_wiki"]["projection_profile_name"] == "standard"
    assert result["living_wiki"]["projection_profile_version"] == "2"
    _validate(root, "projection-profile.v1.schema.json", profile)
    _validate(root, "living-wiki-manifest.v2.schema.json", manifest)


def test_full_profile_emits_per_object_canvas(tmp_path: Path) -> None:
    root = _vault(tmp_path)
    with AutonomousKnowledgeStore(root, read_only=False) as store:
        grant_id = _grant(store)
        objects = [
            store.remember(
                grant_id=grant_id,
                idempotency_key=f"full-canvas-{index}",
                title=f"Full canvas object {index}",
                body="A full profile includes one local canvas per object.",
                kind="concept",
                operation="upsert_concept",
                confirm_no_case_data=True,
            )
            for index in range(2)
        ]
        result = store.rebuild_derived(projection_profile="full")

    paths = sorted((root / "canvas").glob("object-*.canvas"))
    assert len(paths) == len(objects)
    assert result["living_wiki"]["canvas_count"] >= len(objects)
    manifest = json.loads(
        (root / ".deeplaw" / "derived" / "tree" / "living-wiki-manifest.json").read_text()
    )
    profile = projection_profile("full")
    assert manifest["configuration"]["projection_profile"] == profile
    assert manifest["configuration"]["projection_profile_sha256"] == sha256_bytes(
        canonical_json(profile).encode("utf-8")
    )
    assert manifest["configuration"]["projection_profile"]["version"] == "2"
    assert manifest["configuration"]["projection_profile"]["name"] == "full"
    assert manifest["configuration"]["local_canvas_per_object"] is True
    community_pages = list((root / "wiki" / "communities").glob("*.md"))
    assert (root / "wiki" / "communities" / "index.md").is_file()
    assert any(path.name != "index.md" for path in community_pages)
    canvas_names = {path.name for path in (root / "canvas").rglob("*.canvas")}
    assert "knowledge-graph.canvas" in canvas_names
    assert "knowledge-concept.canvas" in canvas_names
    assert any(name.startswith("community-") for name in canvas_names)
    assert {name for name in canvas_names if name.startswith("object-")} == {
        path.name for path in paths
    }
    assert list((root / "wiki" / "concepts").glob("knowledge_*.md"))
    assert result["living_wiki"]["projection_profile_version"] == "2"


@pytest.mark.parametrize("version", ("1", "2"))
def test_projection_profile_and_coordinator_receipt_accept_closed_versions(
    tmp_path: Path,
    version: str,
) -> None:
    root = _vault(tmp_path)
    profile = projection_profile("standard")
    profile["version"] = version
    _validate(root, "projection-profile.v1.schema.json", profile)

    digest = "0" * 64
    living_files = [{"path": "wiki/index.md", "byte_size": 1, "sha256": digest}]
    projection = {
        "files": [],
        "components": [],
        "manifest_sha256": digest,
        "input_audit_head": digest,
        "living_wiki": {
            "schema_version": "deeplaw.living-wiki-manifest/v2",
            "manifest_sha256": digest,
            "knowledge_count": 0,
            "relation_count": 0,
            "source_count": 0,
            "file_count": len(living_files),
            "files": living_files,
            "index_shard_count": 0,
            "canvas_count": 0,
            "community_count": 0,
            "input_audit_head": digest,
            "projection_profile_name": "standard",
            "projection_profile_version": version,
        },
    }
    receipt = CompilationCoordinator._projection_receipt(projection)
    assert receipt["projection_profile_version"] == version
    assert receipt["living_wiki"]["projection_profile_version"] == version


def test_historical_v1_manifest_is_readable_and_rebuilt_as_v2(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = _vault(tmp_path)
    monkeypatch.setitem(projection_profiles._PROFILES["standard"], "version", "1")
    with AutonomousKnowledgeStore(root, read_only=False) as store:
        historical = store.rebuild_derived()
    assert historical["living_wiki"]["projection_profile_version"] == "1"

    monkeypatch.setitem(projection_profiles._PROFILES["standard"], "version", "2")
    with AutonomousKnowledgeStore(root, read_only=False) as store:
        rebuilt = store.rebuild_derived()

    assert rebuilt["living_wiki"]["projection_profile_name"] == "standard"
    assert rebuilt["living_wiki"]["projection_profile_version"] == "2"


def test_full_to_minimal_cleanup_preserves_unregistered_user_file(tmp_path: Path) -> None:
    root = _vault(tmp_path)
    with AutonomousKnowledgeStore(root, read_only=False) as store:
        grant_id = _grant(store)
        store.remember(
            grant_id=grant_id,
            idempotency_key="profile-switch-object",
            title="Profile switch object",
            body="The object canvas is removed when switching to minimal.",
            kind="concept",
            operation="upsert_concept",
            confirm_no_case_data=True,
        )
        store.rebuild_derived(projection_profile="full")

    user_file = root / "canvas" / "user.canvas"
    user_file.write_text('{"nodes": [], "edges": []}\n', encoding="utf-8")
    user_wiki = root / "wiki" / "user-notes.md"
    user_wiki.write_text("# Owner note\n", encoding="utf-8")
    with AutonomousKnowledgeStore(root, read_only=False) as store:
        store.rebuild_derived(projection_profile="minimal")

    assert user_file.is_file()
    assert user_wiki.is_file()
    assert not list((root / "canvas").glob("object-*.canvas"))
    assert not (root / "canvas" / "knowledge-graph.canvas").exists()
    assert not list((root / "wiki" / "indexes").glob("claim*.md"))


def test_invalid_projection_profile_is_rejected(tmp_path: Path) -> None:
    root = _vault(tmp_path)
    with AutonomousKnowledgeStore(root, read_only=False) as store, pytest.raises(
        ValueError, match="projection profile"
    ):
        store.rebuild_derived(projection_profile="invalid")
    with pytest.raises(ValueError, match="projection profile"):
        projection_profile("invalid")


def test_projection_rows_and_relations_share_reference_time(tmp_path: Path) -> None:
    root = _vault(tmp_path)
    with AutonomousKnowledgeStore(root, read_only=False) as store:
        grant_id = _grant(store)
        current = store.remember(
            grant_id=grant_id,
            idempotency_key="reference-current",
            title="Current interval object",
            body="This object is valid at the rebuild reference time.",
            kind="concept",
            operation="upsert_concept",
            confirm_no_case_data=True,
        )
        store.remember(
            grant_id=grant_id,
            idempotency_key="reference-future",
            title="Future interval object",
            body="This object starts after the rebuild reference time.",
            kind="concept",
            valid_from="2999-01-01T00:00:00Z",
            operation="upsert_concept",
            confirm_no_case_data=True,
        )
        store.remember(
            grant_id=grant_id,
            idempotency_key="reference-expired",
            title="Expired interval object",
            body="This object expired before the rebuild reference time.",
            kind="concept",
            valid_to="2000-01-01T00:00:00Z",
            operation="upsert_concept",
            confirm_no_case_data=True,
        )
        result = store.rebuild_derived()

    assert result["living_wiki"]["knowledge_count"] == 1
    assert (root / current["workspace_path"]).is_file()
    assert not list((root / "wiki" / "concepts").glob("knowledge_*.md"))
