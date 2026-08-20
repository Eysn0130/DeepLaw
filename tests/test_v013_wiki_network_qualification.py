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
MUTATION_OPERATIONS = tuple(sorted(set(KIND_OPERATIONS.values()) | {"add_relation", "record_run"}))


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
        assert record["canonical_page_path"].split("/", 1)[0] in {
            "knowledge",
            "memory",
            "skills",
        }
        assert result["knowledge_id"] in record["canonical_page_path"]
        resolved = resolver.resolve(
            {"knowledge_id": result["knowledge_id"], "allowed_freshness": ["fresh", "unknown"]}
        )
        assert resolved["status"] == "resolved"
        assert resolved["admission"]["admitted"] is True
        assert resolved["candidates"][0]["page_id"] == record["page_id"]
        registered_page = KnowledgeOS.open(root).wiki.page(record["canonical_page_path"])
        assert registered_page["wiki_path"] == record["canonical_page_path"]

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
    assert all(
        item["path"] != f"wiki/concepts/{concept_before['knowledge_id']}.md"
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
def test_v013_wiki_network_wrong_merge_stays_identity_ambiguous(tmp_path: Path) -> None:
    """Same titles must retain distinct semantic identities and pages."""

    from deeplaw.api import KnowledgeOS
    from deeplaw.knowledge_mcp_server import handle_knowledge_support
    from deeplaw.util import canonical_json

    root = _vault(tmp_path)
    grant_id = _grant(root)
    first = _sink(
        root,
        grant_id,
        {
            "operation": "upsert_concept",
            "idempotency_key": "wiki-network-wrong-merge-a",
            "title": "Shared qualification title",
            "body": "First synthetic object with its own semantic identity.",
            "scope": "project",
            "sensitivity": "private",
            "semantic_key": "v013:wrong-merge:first",
        },
    )
    second = _sink(
        root,
        grant_id,
        {
            "operation": "upsert_concept",
            "idempotency_key": "wiki-network-wrong-merge-b",
            "title": "Shared qualification title",
            "body": "Second synthetic object with its own semantic identity.",
            "scope": "project",
            "sensitivity": "private",
            "semantic_key": "v013:wrong-merge:second",
        },
    )

    assert first["knowledge_id"] != second["knowledge_id"]
    assert first["revision_id"] != second["revision_id"]
    for result in (first, second):
        assert result["origin"] == "agent_derived"
        assert result["authority"] == "agent_derived"
        assert result["legal_authority"] is False
        assert result["lifecycle"] == "active"
        assert result["scope"] == "project"
        assert result["sensitivity"] == "private"
        assert result["source_free"] is True

    from deeplaw.knowledge_autonomy import AutonomousKnowledgeStore

    with AutonomousKnowledgeStore(root, read_only=False) as store:
        rebuilt = store.rebuild_derived(projection_profile="standard")
    assert rebuilt["living_wiki"]["projection_profile_name"] == "standard"

    snapshot = _artifact_snapshot(root)
    records = {
        record["knowledge_id"]: record
        for record in snapshot["registry"]["records"]
        if record.get("knowledge_id") in {first["knowledge_id"], second["knowledge_id"]}
    }
    assert set(records) == {first["knowledge_id"], second["knowledge_id"]}
    assert records[first["knowledge_id"]]["canonical_page_path"] != records[
        second["knowledge_id"]
    ]["canonical_page_path"]
    assert records[first["knowledge_id"]]["semantic_key"] != records[second["knowledge_id"]][
        "semantic_key"
    ]
    with KnowledgeOS.open(root) as knowledge_os:
        for result in (first, second):
            page = knowledge_os.wiki.page(records[result["knowledge_id"]]["canonical_page_path"])
            assert page["write_performed"] is False
            assert result["knowledge_id"] in page["content"]

    identity = handle_knowledge_support(
        operation="identity_lookup",
        query="Shared qualification title",
        scope="project",
        max_sensitivity="private",
        limit=2,
        vault_path=root,
    )
    assert len(canonical_json(identity).encode("utf-8")) <= 65_536
    lookup = identity["result"]
    assert lookup["status"] == "ambiguous"
    assert lookup["candidate_count"] == 2
    assert {item["knowledge_id"] for item in lookup["candidates"]} == set(records)
    assert all(item["authority"] == "agent_derived" for item in lookup["candidates"])
    assert all(item["legal_authority"] is False for item in lookup["candidates"])


@pytest.mark.qualification
def test_v013_wiki_network_alias_collision_is_explicitly_ambiguous(tmp_path: Path) -> None:
    """A shared public alias must never silently select one Wiki identity."""

    from deeplaw.knowledge_autonomy import AutonomousKnowledgeStore
    from deeplaw.knowledge_mcp_server import handle_knowledge_support
    from deeplaw.util import canonical_json

    root = _vault(tmp_path)
    grant_id = _grant(root)
    shared_alias = "v013 qualification shared alias"
    first = _sink(
        root,
        grant_id,
        {
            "operation": "upsert_concept",
            "idempotency_key": "wiki-network-alias-a",
            "title": "Alias target Alpha",
            "body": "First alias target.",
            "scope": "project",
            "sensitivity": "private",
            "semantic_key": "v013:alias-collision:first",
            "aliases": [shared_alias],
        },
    )
    second = _sink(
        root,
        grant_id,
        {
            "operation": "upsert_concept",
            "idempotency_key": "wiki-network-alias-b",
            "title": "Alias target Beta",
            "body": "Second alias target.",
            "scope": "project",
            "sensitivity": "private",
            "semantic_key": "v013:alias-collision:second",
            "aliases": [shared_alias],
        },
    )
    linker = _sink(
        root,
        grant_id,
        {
            "operation": "upsert_concept",
            "idempotency_key": "wiki-network-alias-linker",
            "title": "Alias collision linker",
            "body": f"A bounded link points at [[{shared_alias}]].",
            "scope": "project",
            "sensitivity": "private",
            "semantic_key": "v013:alias-collision:linker",
        },
    )
    assert len({first["knowledge_id"], second["knowledge_id"], linker["knowledge_id"]}) == 3
    for result in (first, second, linker):
        assert result["origin"] == "agent_derived"
        assert result["authority"] == "agent_derived"
        assert result["legal_authority"] is False
        assert result["lifecycle"] == "active"
        assert result["scope"] == "project"
        assert result["sensitivity"] == "private"
        assert result["source_free"] is True

    owner_note = root / "wiki" / "v013-alias-owner-note.md"
    owner_note.write_text("# Alias owner note\nPreserve this user file.\n", encoding="utf-8")
    owner_note_bytes = owner_note.read_bytes()
    with AutonomousKnowledgeStore(root, read_only=False) as store:
        store.rebuild_derived(projection_profile="standard")
    assert owner_note.read_bytes() == owner_note_bytes

    identity = handle_knowledge_support(
        operation="identity_lookup",
        query=shared_alias,
        scope="project",
        max_sensitivity="private",
        limit=2,
        vault_path=root,
    )
    assert len(canonical_json(identity).encode("utf-8")) <= 65_536
    lookup = identity["result"]
    assert lookup["status"] == "ambiguous"
    assert lookup["candidate_count"] == 2
    target_ids = {first["knowledge_id"], second["knowledge_id"]}
    assert {item["knowledge_id"] for item in lookup["candidates"]} == target_ids
    assert all(item["authority"] == "agent_derived" for item in lookup["candidates"])
    assert all(item["legal_authority"] is False for item in lookup["candidates"])

    snapshot = _artifact_snapshot(root)
    resolver = snapshot["resolver"]
    resolved = resolver.resolve(
        {"alias": shared_alias},
        scope="project",
        max_sensitivity="private",
        allowed_freshness=["fresh", "unknown"],
    )
    assert resolved["status"] == "ambiguous"
    assert resolved["ambiguity"] == {"reason": "multiple_candidates", "candidate_count": 2}
    assert {item["page_id"] for item in resolved["candidates"]} == target_ids

    alias_edges = [
        edge
        for edge in snapshot["links"]["edges"]
        if edge["source_page_id"] == linker["knowledge_id"]
        and edge["target_raw"] == shared_alias
    ]
    assert len(alias_edges) == 1
    # Source-free derived targets have ``unknown`` freshness.  Link-index construction uses the
    # resolver's fail-closed default (``fresh``), so it must not disclose denied candidates.  The
    # owner/operator resolver call above explicitly admits ``unknown`` and is the public seam that
    # proves the alias collision is ambiguous rather than merged.
    assert alias_edges[0]["status"] == "out_of_scope"
    assert alias_edges[0]["candidate_count"] == 0
    assert alias_edges[0]["target_page_ids"] == []


@pytest.mark.qualification
def test_v013_wiki_network_relation_cycle_uses_canonical_graph_revisions(
    tmp_path: Path,
) -> None:
    """Typed A -> B -> C -> A relations survive bounded graph and Wiki rebuilds."""

    from deeplaw.api import KnowledgeOS
    from deeplaw.knowledge_autonomy import AutonomousKnowledgeStore
    from deeplaw.knowledge_mcp_server import handle_knowledge_support
    from deeplaw.util import canonical_json, sha256_bytes

    root = _vault(tmp_path)
    grant_id = _grant(root)
    nodes: list[dict[str, Any]] = []
    for index in range(3):
        nodes.append(
            _sink(
                root,
                grant_id,
                {
                    "operation": "upsert_concept",
                    "idempotency_key": f"wiki-network-cycle-node-{index}",
                    "title": f"Cycle node {index}",
                    "body": f"Synthetic cycle node {index}.",
                    "scope": "project",
                    "sensitivity": "private",
                    "semantic_key": f"v013:cycle:node:{index}",
                },
            )
        )
    predicates = ("depends_on", "supports", "implements")
    relations: list[dict[str, Any]] = []
    for index, predicate in enumerate(predicates):
        relation = _sink(
            root,
            grant_id,
            {
                "operation": "add_relation",
                "idempotency_key": f"wiki-network-cycle-relation-{index}",
                "subject_knowledge_id": nodes[index]["knowledge_id"],
                "predicate": predicate,
                "object_knowledge_id": nodes[(index + 1) % 3]["knowledge_id"],
                "evidence_refs": [{"revision_id": nodes[index]["revision_id"]}],
            },
        )
        relations.append(relation)
        assert relation["lifecycle"] == "active"
        assert relation["origin"] == "agent_derived"
        assert relation["authority"] == "agent_derived"
        assert relation["legal_authority"] is False
        assert relation["scope"] == "project"
        assert relation["sensitivity"] == "private"
        assert relation["source_free"] is False

    owner_note = root / "wiki" / "v013-cycle-owner-note.md"
    owner_note.write_text("# Cycle owner note\nPreserve this user file.\n", encoding="utf-8")
    owner_note_bytes = owner_note.read_bytes()
    with AutonomousKnowledgeStore(root, read_only=False) as store:
        rebuilt = store.rebuild_derived(projection_profile="full")
    assert rebuilt["living_wiki"]["projection_profile_name"] == "full"
    assert owner_note.read_bytes() == owner_note_bytes

    graph_bounded_response = handle_knowledge_support(
        operation="graph",
        scope="project",
        max_sensitivity="private",
        limit=2,
        vault_path=root,
    )
    assert len(canonical_json(graph_bounded_response).encode("utf-8")) <= 65_536
    graph_bounded = graph_bounded_response["result"]
    assert graph_bounded["canonical_relation_revisions"] is True
    assert graph_bounded["derived_adjacency"] is True
    assert graph_bounded["budget"]["max_relations"] == 2
    assert graph_bounded["budget"]["selected_relations"] == 2
    assert len(graph_bounded["relations"]) == 2
    assert graph_bounded["budget"]["candidate_scan_truncated"] is False

    graph_response = handle_knowledge_support(
        operation="graph",
        scope="project",
        max_sensitivity="private",
        limit=3,
        vault_path=root,
    )
    graph = graph_response["result"]
    expected_edges = {
        (
            nodes[index]["knowledge_id"],
            predicates[index],
            nodes[(index + 1) % 3]["knowledge_id"],
        )
        for index in range(3)
    }
    assert {
        (item["subject_knowledge_id"], item["predicate"], item["object_knowledge_id"])
        for item in graph["relations"]
    } == expected_edges
    relation_ids = [item["relation_revision_id"] for item in graph["relations"]]
    assert {item["relation_revision_id"] for item in relations} == set(relation_ids)
    assert graph["audit_head"] == relations[-1]["audit_head"]

    verify = handle_knowledge_support(operation="verify", vault_path=root)
    assert len(canonical_json(verify).encode("utf-8")) <= 65_536
    assert verify["result"]["valid"] is True
    assert verify["result"]["autonomous_core"]["audit_head"] == graph["audit_head"]

    v2_manifest = _json(root, ".deeplaw/derived/tree/living-wiki-manifest.json")
    assert v2_manifest["relation_revision_count"] == 3
    assert v2_manifest["relation_revision_ids_sha256"] == sha256_bytes(
        canonical_json(relation_ids).encode("utf-8")
    )

    snapshot = _artifact_snapshot(root)
    registry = snapshot["registry"]
    path_by_id = {
        record["knowledge_id"]: record["canonical_page_path"]
        for record in registry["records"]
        if record.get("knowledge_id") in {node["knowledge_id"] for node in nodes}
    }
    assert len(path_by_id) == 3
    for relation in graph["relations"]:
        page_text = (root / path_by_id[relation["subject_knowledge_id"]]).read_text(
            encoding="utf-8"
        )
        assert relation["relation_revision_id"] in page_text
        target_path = path_by_id[relation["object_knowledge_id"]].removesuffix(".md")
        matching_edges = [
            edge
            for edge in snapshot["links"]["edges"]
            if edge["source_page_id"] == relation["subject_knowledge_id"]
            and edge["target_raw"] == target_path
        ]
        assert matching_edges
        assert all(edge["link_type"] == "wikilink" for edge in matching_edges)

    with KnowledgeOS.open(root) as knowledge_os:
        local_graph = knowledge_os.wiki.local_graph(nodes[0]["knowledge_id"], limit=2)
    assert local_graph["canonical_relation_revisions"] is True
    assert local_graph["budget"]["max_relations"] == 2
    assert len(local_graph["relations"]) == 2
    assert local_graph["derived_adjacency"] is True
