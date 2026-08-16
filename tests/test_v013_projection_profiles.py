from __future__ import annotations

import json
from pathlib import Path

import pytest
from jsonschema import Draft202012Validator, FormatChecker

from deeplaw.knowledge_autonomy import SINK_OPERATIONS, AutonomousKnowledgeStore
from deeplaw.knowledge_store import initialize_knowledge_vault
from deeplaw.projection.profiles import projection_profile


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
            body="The default projection keeps sharded indexes and shared canvases.",
            kind="concept",
            operation="upsert_concept",
            confirm_no_case_data=True,
        )
        result = store.rebuild_derived()

    profile = projection_profile("standard")
    manifest_path = root / ".deeplaw" / "derived" / "tree" / "living-wiki-manifest.json"
    manifest = json.loads(manifest_path.read_text())
    assert manifest["schema_version"] == "deeplaw.living-wiki-manifest/v2"
    assert manifest["configuration"]["projection_profile"] == profile
    assert manifest["configuration"]["local_canvas_per_object"] is False
    assert (root / "canvas" / "knowledge-graph.canvas").is_file()
    assert not list((root / "canvas").glob("object-*.canvas"))
    assert result["living_wiki"]["projection_profile_name"] == "standard"
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
    assert manifest["configuration"]["projection_profile"]["name"] == "full"
    assert manifest["configuration"]["local_canvas_per_object"] is True


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
        future = store.remember(
            grant_id=grant_id,
            idempotency_key="reference-future",
            title="Future interval object",
            body="This object starts after the rebuild reference time.",
            kind="concept",
            valid_from="2999-01-01T00:00:00Z",
            operation="upsert_concept",
            confirm_no_case_data=True,
        )
        expired = store.remember(
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
    assert (root / "wiki" / "concepts" / f"{current['knowledge_id']}.md").is_file()
    assert not (root / "wiki" / "concepts" / f"{future['knowledge_id']}.md").exists()
    assert not (root / "wiki" / "concepts" / f"{expired['knowledge_id']}.md").exists()
