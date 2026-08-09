"""Development-only v0.13 Living Wiki network qualification.

This fixture is repository-visible synthetic evidence.  It is not release, RC, GA, or
competitive-quality evidence.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

EXPECTED_KINDS = {
    "claim",
    "concept",
    "entity",
    "event",
    "decision",
    "procedure",
    "experience",
    "preference",
    "synthesis",
    "comparison",
    "skill",
    "memory",
}
KIND_OPERATIONS = {
    "claim": "save_claim",
    "concept": "upsert_concept",
    "entity": "upsert_entity",
    "event": "record_event",
    "decision": "remember",
    "procedure": "remember",
    "experience": "remember",
    "preference": "remember",
    "synthesis": "save_synthesis",
    "comparison": "save_comparison",
    "skill": "save_skill",
    "memory": "remember",
}
MUTATION_OPERATIONS = tuple(sorted(set(KIND_OPERATIONS.values()) | {"record_run"}))


def _vault(tmp_path: Path) -> Path:
    from deeplaw.knowledge_autonomy import initialize_autonomous_core
    from deeplaw.knowledge_store import initialize_knowledge_vault

    root = tmp_path / "vault"
    initialize_knowledge_vault(root, name="v013-wiki-network", scope="project")
    initialize_autonomous_core(root)
    return root


def _grant(root: Path) -> str:
    from deeplaw.knowledge_autonomy import AutonomousKnowledgeStore

    with AutonomousKnowledgeStore(root, read_only=False) as store:
        return store.enable_grant(
            writer_id="v013-wiki-network-qualification",
            operations=MUTATION_OPERATIONS,
            max_mutations_per_minute=120,
        )["grant_id"]


def _sink(root: Path, grant_id: str, request: dict[str, Any]) -> dict[str, Any]:
    from deeplaw.knowledge_sink_mcp_server import handle_knowledge_sink

    request = {**request, "confirm_no_case_data": True}
    response = handle_knowledge_sink(request, grant_id=grant_id, vault_path=root)
    assert response["boundary"]["case_data_allowed"] is False
    result = response["result"]
    assert isinstance(result, dict)
    return result


def _skill_manifest() -> dict[str, Any]:
    return {
        "purpose": "Navigate a bounded synthetic Wiki fixture.",
        "applies_to": ["synthetic Wiki network checks"],
        "does_not_apply_to": ["live legal or client material"],
        "invocation_mode": "user-invoked",
        "input_contract": {"type": "object"},
        "output_contract": {"type": "object"},
        "capabilities": ["read knowledge"],
        "resource_limits": {"items": 12},
        "steps": [
            {
                "instruction": "Inspect the synthetic Wiki network.",
                "completion_criterion": "The requested page identity is resolved.",
            }
        ],
        "success_criteria": ["The requested page is resolved."],
        "failure_conditions": ["The requested page is unavailable."],
        "license": "MIT",
        "host_compatibility": ["codex"],
        "verification_commands": ["uv run --frozen pytest -q"],
        "known_limitations": ["Synthetic development fixture only."],
        "lifecycle": "draft",
        "source_revision_ids": [],
        "evaluation_run_ids": [],
        "supersedes_skill_revision": None,
        "deprecation_reason": None,
    }


def _seed_all_kinds(root: Path, grant_id: str) -> dict[str, dict[str, Any]]:
    assert set(KIND_OPERATIONS) == EXPECTED_KINDS
    _sink(
        root,
        grant_id,
        {
            "operation": "record_run",
            "idempotency_key": "wiki-network-run",
            "run_id": "v013-wiki-network-run",
            "task": "Construct a synthetic Wiki network qualification fixture.",
            "host_id": "v013-wiki-network-test",
            "status": "succeeded",
            "scope": "project",
            "sensitivity": "private",
            "run_metadata": {},
        },
    )
    results: dict[str, dict[str, Any]] = {}
    for kind in sorted(EXPECTED_KINDS):
        operation = KIND_OPERATIONS[kind]
        request: dict[str, Any] = {
            "operation": operation,
            "idempotency_key": f"wiki-network-{kind}",
            "title": f"Wiki network {kind}",
            "body": f"Persistent synthetic Knowledge Object for {kind}.",
            "scope": "project",
            "sensitivity": "private",
            "semantic_key": f"v013:wiki-network:{kind}",
        }
        # Forced operation branches derive the kind from the operation and reject a redundant
        # ``kind`` field.  Generic ``remember`` carries the explicit kind for the remaining
        # Knowledge Object variants.
        if operation == "remember":
            request["kind"] = kind
        if kind == "claim":
            request["run_id"] = "v013-wiki-network-run"
        if kind == "preference":
            request["preference_basis"] = "agent_inference"
        if kind == "memory":
            request["memory_type"] = "semantic"
        if kind == "skill":
            request["skill_manifest"] = _skill_manifest()
        result = _sink(root, grant_id, request)
        assert result["kind"] == kind
        assert result["knowledge_id"].startswith("knowledge_")
        assert result["revision_id"].startswith("knowledgerev_")
        results[kind] = result
    return results


def _json(root: Path, relative: str) -> dict[str, Any]:
    value = json.loads((root / relative).read_text(encoding="utf-8"))
    assert isinstance(value, dict)
    return value


def _artifact_snapshot(root: Path) -> dict[str, Any]:
    """Capture hashes from only manifest-declared Wiki v2/v3 artifacts.

    No timestamp or receipt field is omitted: ``generated_at`` is bound to the input audit
    event, so an unchanged rebuild must produce the exact same canonical bytes and hashes.
    """

    from deeplaw.util import canonical_json, sha256_bytes
    from deeplaw.wiki import load_link_index, load_page_registry, load_resolver

    v2 = _json(root, ".deeplaw/derived/tree/living-wiki-manifest.json")
    v3 = _json(root, ".deeplaw/derived/wiki/v3/manifest.json")
    registry = load_page_registry(root, v3)
    links = load_link_index(root, v3, registry)
    resolver = load_resolver(root, v3, registry)

    declared_hashes: dict[str, str] = {}
    for descriptor in v3["components"]:
        component_path = descriptor["manifest_path"]
        component_bytes = (root / component_path).read_bytes()
        declared_hashes[component_path] = sha256_bytes(component_bytes)
        component_manifest = json.loads(component_bytes)
        assert isinstance(component_manifest, dict)
        for shard in [
            *component_manifest.get("shards", []),
            *component_manifest.get("coverage_shards", []),
        ]:
            shard_path = shard["path"]
            declared_hashes[shard_path] = sha256_bytes((root / shard_path).read_bytes())

    v2_file_hashes = {
        item["path"]: sha256_bytes((root / item["path"]).read_bytes())
        for item in v2["files"]
    }
    page_hashes = {
        record["canonical_page_path"]: sha256_bytes(
            (root / record["canonical_page_path"]).read_bytes()
        )
        for record in registry["records"]
    }
    link_content = {
        "edges": list(links["edges"]),
        "coverage": list(links["coverage"]),
    }
    return {
        "v2_manifest_sha256": v2["manifest_sha256"],
        "v3_manifest_sha256": v3["manifest_sha256"],
        "v2_manifest_canonical_sha256": sha256_bytes(canonical_json(v2).encode("utf-8")),
        "v3_manifest_canonical_sha256": sha256_bytes(canonical_json(v3).encode("utf-8")),
        "registry_sha256": registry["registry_sha256"],
        "registry_records_sha256": sha256_bytes(
            canonical_json(registry["records"]).encode("utf-8")
        ),
        "link_index_sha256": links["index_sha256"],
        "link_content_sha256": sha256_bytes(canonical_json(link_content).encode("utf-8")),
        "resolver_index_sha256": resolver.index_sha256,
        "resolver_records_sha256": sha256_bytes(
            canonical_json(resolver.records).encode("utf-8")
        ),
        "declared_v3_hashes": declared_hashes,
        "v2_file_hashes": v2_file_hashes,
        "page_hashes": page_hashes,
        "registry": registry,
        "links": links,
        "resolver": resolver,
    }


def _artifact_hash_view(snapshot: dict[str, Any]) -> dict[str, Any]:
    """Drop live loader handles before comparing the serializable hash snapshot."""

    return {
        key: value
        for key, value in snapshot.items()
        if key not in {"registry", "links", "resolver"}
    }


def _assert_network(root: Path, objects: dict[str, dict[str, Any]]) -> dict[str, Any]:
    from deeplaw.api import KnowledgeOS

    snapshot = _artifact_snapshot(root)
    registry = snapshot["registry"]
    resolver = snapshot["resolver"]
    by_knowledge_id = {
        record.get("knowledge_id"): record
        for record in registry["records"]
        if record.get("namespace") == "knowledge"
    }
    assert set(by_knowledge_id) == {result["knowledge_id"] for result in objects.values()}
    for kind, result in objects.items():
        browse = KnowledgeOS.open(root).wiki.browse_kind(kind)
        assert browse["write_performed"] is False
        assert any(item["knowledge_id"] == result["knowledge_id"] for item in browse["items"])
        record = by_knowledge_id[result["knowledge_id"]]
        assert record["kind"] == kind
        assert record["revision_id"] == result["revision_id"]
        assert record["canonical_page_path"].startswith("wiki/")
        assert record["canonical_page_path"].endswith(f"/{result['knowledge_id']}.md")
        resolved = resolver.resolve(
            {"knowledge_id": result["knowledge_id"], "allowed_freshness": ["fresh", "unknown"]}
        )
        assert resolved["status"] == "resolved"
        assert resolved["admission"]["admitted"] is True
        assert resolved["candidates"][0]["page_id"] == record["page_id"]

    # The API read path must consume the indexed network, rather than a filesystem scan.
    root_page = KnowledgeOS.open(root).wiki.page("wiki/index.md")
    assert root_page["wiki_path"] == "wiki/index.md"
    outlinks = KnowledgeOS.open(root).wiki.outlinks("wiki/index.md")
    assert outlinks["index_used"] is True
    assert outlinks["total_count"] > 0
    assert snapshot["links"]["component"]["coverage_record_count"] == registry["component"][
        "page_count"
    ]
    assert any(edge["status"] == "resolved" for edge in snapshot["links"]["edges"])
    return snapshot


def test_v013_development_wiki_network_qualification(tmp_path: Path) -> None:
    """Exercise all Knowledge Kinds through the mutation and read API seams."""

    from deeplaw.knowledge_autonomy import AutonomousKnowledgeStore

    root = _vault(tmp_path)
    grant_id = _grant(root)
    objects = _seed_all_kinds(root, grant_id)

    with AutonomousKnowledgeStore(root, read_only=False) as store:
        full_rebuild = store.rebuild_derived(projection_profile="standard")
    assert full_rebuild["living_wiki"]["projection_profile_name"] == "standard"
    first = _assert_network(root, objects)

    user_note = root / "wiki" / "v013-owner-note.md"
    user_note.write_text("# Owner note\nThis file is not projection-owned.\n", encoding="utf-8")
    user_note_bytes = user_note.read_bytes()
    user_note_relative = user_note.relative_to(root).as_posix()
    assert user_note_relative not in first["v2_file_hashes"]
    assert user_note_relative not in first["page_hashes"]

    with AutonomousKnowledgeStore(root, read_only=False) as store:
        no_op = store.rebuild_derived(projection_profile="standard")
    assert no_op["living_wiki"]["change_set"]["created"] == []
    assert no_op["living_wiki"]["change_set"]["updated"] == []
    assert no_op["living_wiki"]["change_set"]["deleted"] == []
    assert len(no_op["living_wiki"]["change_set"]["unchanged"]) > 0
    assert _artifact_hash_view(_artifact_snapshot(root)) == _artifact_hash_view(first)
    assert user_note.read_bytes() == user_note_bytes

    concept_before = objects["concept"]
    updated = _sink(
        root,
        grant_id,
        {
            "operation": "upsert_concept",
            "idempotency_key": "wiki-network-concept-revision-2",
            "knowledge_id": concept_before["knowledge_id"],
            "expected_revision_id": concept_before["revision_id"],
            "title": "Wiki network concept revised",
            "body": "Persistent synthetic concept revision two.",
            "scope": "project",
            "sensitivity": "private",
            "semantic_key": "v013:wiki-network:concept",
        },
    )
    assert updated["knowledge_id"] == concept_before["knowledge_id"]
    assert updated["revision_id"] != concept_before["revision_id"]

    with AutonomousKnowledgeStore(root, read_only=False) as store:
        incremental = store.rebuild_derived(projection_profile="standard")
    assert any(
        item["path"] == f"wiki/concepts/{concept_before['knowledge_id']}.md"
        for item in incremental["living_wiki"]["change_set"]["updated"]
    )
    objects["concept"] = updated
    second = _assert_network(root, objects)
    concept_record = next(
        record
        for record in second["registry"]["records"]
        if record.get("knowledge_id") == concept_before["knowledge_id"]
    )
    old_concept_record = next(
        record
        for record in first["registry"]["records"]
        if record.get("knowledge_id") == concept_before["knowledge_id"]
    )
    assert concept_record["page_id"] == old_concept_record["page_id"]
    assert concept_record["canonical_page_path"] == old_concept_record["canonical_page_path"]
    assert concept_record["revision_id"] == updated["revision_id"]
    with AutonomousKnowledgeStore(root, read_only=True) as store:
        assert store.get_current(concept_before["knowledge_id"])["revision_id"] == updated[
            "revision_id"
        ]

    with AutonomousKnowledgeStore(root, read_only=False) as store:
        final_no_op = store.rebuild_derived(projection_profile="standard")
    assert final_no_op["living_wiki"]["change_set"]["created"] == []
    assert final_no_op["living_wiki"]["change_set"]["updated"] == []
    assert final_no_op["living_wiki"]["change_set"]["deleted"] == []
    assert _artifact_hash_view(_artifact_snapshot(root)) == _artifact_hash_view(second)
    assert user_note.read_bytes() == user_note_bytes


@pytest.mark.qualification
@pytest.mark.parametrize(
    ("uncovered_case", "reason"),
    (
        (
            "wrong_merge",
            "The public mutation seam cannot safely synthesize a wrong-merge identity fixture "
            "without bypassing the coordinator.",
        ),
        (
            "alias_collision",
            "The public fixture seam does not expose an owner-approved alias-collision setup; "
            "do not manufacture one with private Ledger writes.",
        ),
        (
            "cycle",
            "The governed cycle/contradiction fixture is executed by the dedicated Query/Graph "
            "qualification; this all-kinds Wiki fixture does not duplicate that mutation lane.",
        ),
    ),
)
def test_v013_wiki_network_uncovered_cases_are_explicitly_skipped(
    uncovered_case: str,
    reason: str,
) -> None:
    del uncovered_case
    pytest.skip(reason)
