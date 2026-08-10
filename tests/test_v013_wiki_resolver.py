from __future__ import annotations

import json
from pathlib import Path

from jsonschema import Draft202012Validator

from deeplaw.util import sha256_bytes
from deeplaw.wiki import StableResolver, build_page_registry, build_resolver_index

ZERO = "0" * 64


def _page(page_id: str, path: str, **extra: object) -> dict:
    body = b"plain"
    result = {
        "page_id": page_id,
        "namespace": "knowledge",
        "canonical_page_path": path,
        "kind": "concept",
        "revision_id": f"revision_{page_id}",
        "audit_head": ZERO,
        "byte_size": len(body),
        "sha256": sha256_bytes(body),
        "scope": "project",
        "sensitivity": "public",
        "lifecycle": "active",
        "freshness": "fresh",
        "authority": "none",
        "input_refs": [page_id],
        **extra,
    }
    if "source_fragment" in extra:
        result.pop("revision_id", None)
    return result


def _resolver(*pages: dict) -> StableResolver:
    registry = build_page_registry(
        pages,
        v2_file_inventory=[
            {
                "path": page["canonical_page_path"],
                "byte_size": page["byte_size"],
                "sha256": page["sha256"],
            }
            for page in pages
        ],
        input_audit_head=ZERO,
        legacy_audit_head=ZERO,
        v2_manifest_sha256=ZERO,
        generated_at="2026-08-08T00:00:00Z",
    )
    return StableResolver(registry)


def test_all_explicit_identity_channels_and_bare_locator_rejection() -> None:
    resolver = _resolver(
        _page("knowledge_one", "one.md", semantic_key="alpha", aliases=["same"]),
        _page(
            "knowledge_two",
            "two.md",
            semantic_key="beta",
            source_fragment={"source_revision_id": "source_rev", "fragment_id": "fragment_1"},
        ),
    )
    assert resolver.resolve({"knowledge_id": "knowledge_one"})["status"] == "resolved"
    assert resolver.resolve({"revision_id": "revision_knowledge_one"})["status"] == "resolved"
    assert resolver.resolve({"semantic_key": "alpha"})["status"] == "resolved"
    assert resolver.resolve({"wiki_path": "one.md"})["status"] == "resolved"
    assert resolver.resolve({"wikilink": "one.md"})["status"] == "resolved"
    source_result = resolver.resolve(
        {"source_fragment": {"source_revision_id": "source_rev", "fragment_id": "fragment_1"}}
    )
    assert source_result["status"] == "resolved"
    candidate = source_result["candidates"][0]
    assert set(candidate) == {
        "page_id",
        "canonical_page_path",
        "namespace",
        "kind",
        "title",
        "current_revision",
        "freshness",
        "sensitivity",
        "scope",
        "lifecycle",
        "audit_head",
        "reason",
        "authority",
    }
    assert candidate["current_revision"] == {
        "source_revision_id": "source_rev",
        "fragment_id": "fragment_1",
    }
    assert candidate["authority"] == "none"
    schema = json.loads(Path("contracts/living-wiki-resolver.v1.schema.json").read_text())
    Draft202012Validator(schema).validate(source_result)
    assert (
        resolver.resolve({"source_fragment": {"fragment_id": "fragment_1"}})["status"] == "invalid"
    )


def test_source_revision_id_resolves_only_the_canonical_source_page() -> None:
    source_revision = "source_rev"
    resolver = _resolver(
        _page(
            "source_page",
            "sources/source.md",
            namespace="source",
            kind="source",
            revision_id=source_revision,
        ),
        _page(
            "fragment_page",
            "knowledge/fragment.md",
            source_fragment={"source_revision_id": source_revision, "fragment_id": "fragment_1"},
        ),
    )
    result = resolver.resolve({"source_revision_id": source_revision})
    assert result["status"] == "resolved"
    assert result["candidate_count"] == 1
    assert result["candidates"][0]["page_id"] == "source_page"

    no_source_page = _resolver(
        _page(
            "fragment_page",
            "knowledge/fragment.md",
            source_fragment={"source_revision_id": source_revision, "fragment_id": "fragment_1"},
        )
    )
    assert no_source_page.resolve({"source_revision_id": source_revision})["status"] == "not_found"


def test_source_fragment_anchor_resolves_page_and_anchor_without_fragment_pages() -> None:
    resolver = _resolver(
        _page(
            "source_index",
            "indexes/source.md",
            namespace="aggregate",
            kind="aggregate",
            anchors=[
                {
                    "anchor_id": "fragment_1",
                    "anchor": "fragment:fragment_1",
                    "kind": "source_fragment",
                    "source_fragment": {
                        "source_revision_id": "source_rev",
                        "fragment_id": "fragment_1",
                    },
                }
            ],
        )
    )
    result = resolver.resolve(
        {"source_fragment": {"source_revision_id": "source_rev", "fragment_id": "fragment_1"}}
    )
    assert result["status"] == "resolved"
    assert result["candidates"][0]["page_id"] == "source_index"
    assert result["candidates"][0]["anchor"]["anchor_id"] == "fragment_1"

    revision_resolver = _resolver(
        _page(
            "source_index",
            "indexes/source-revision.md",
            namespace="aggregate",
            kind="aggregate",
            anchors=[
                {
                    "anchor_id": "fragment-revision_1",
                    "anchor": "fragment-1",
                    "kind": "source_fragment",
                    "source_fragment": {
                        "source_revision_id": "source_rev",
                        "fragment_revision_id": "fragment-revision_1",
                    },
                }
            ],
        )
    )
    revision_result = revision_resolver.resolve(
        {
            "source_fragment": {
                "source_revision_id": "source_rev",
                "fragment_revision_id": "fragment-revision_1",
            }
        }
    )
    assert revision_result["status"] == "resolved"
    assert revision_result["candidates"][0]["anchor"]["anchor"] == "fragment-1"


def test_alias_ambiguity_never_becomes_resolved_by_limit() -> None:
    resolver = _resolver(
        _page("knowledge_one", "one.md", aliases=["same"]),
        _page("knowledge_two", "two.md", aliases=["same"]),
    )
    result = resolver.resolve({"alias": "same"}, limit=1)
    assert result["status"] == "ambiguous"
    assert result["candidate_count"] == 2
    assert result["candidates_truncated"] is True
    assert result["truncation_reason"] == "candidate_limit"


def test_multilingual_alias_is_exact_and_never_wrong_merges() -> None:
    resolver = _resolver(
        _page(
            "knowledge_policy_mainland",
            "knowledge/policy-mainland.md",
            title="数据保留政策",
            aliases=["資料保留政策", "保留政策"],
        ),
        _page(
            "knowledge_policy_harbor",
            "knowledge/policy-harbor.md",
            title="港口保留政策",
            aliases=["保留政策"],
        ),
    )

    exact = resolver.resolve({"alias": "資料保留政策"})
    assert exact["status"] == "resolved"
    assert exact["candidates"][0]["page_id"] == "knowledge_policy_mainland"

    ambiguous = resolver.resolve({"alias": "保留政策"}, limit=1)
    assert ambiguous["status"] == "ambiguous"
    assert ambiguous["candidate_count"] == 2
    assert ambiguous["candidates_truncated"] is True


def test_admission_and_deferred_namespaces_fail_closed() -> None:
    resolver = _resolver(
        _page("knowledge_private", "private.md", scope="personal", sensitivity="private"),
        _page("knowledge_stale", "stale.md", freshness="stale"),
    )
    denied = resolver.resolve({"knowledge_id": "knowledge_private"})
    assert denied["status"] == "not_admitted"
    assert denied["candidate_count"] == 0
    assert denied["candidates"] == []
    assert "knowledge_private" not in json.dumps(denied["receipt"], sort_keys=True)
    restricted = resolver.resolve(
        {
            "knowledge_id": "knowledge_private",
            "allowed_scopes": ["project"],
            "max_sensitivity": "public",
        }
    )
    assert restricted["status"] == "not_admitted"
    assert restricted["candidate_count"] == 0
    assert restricted["candidates"] == []
    assert (
        resolver.resolve(
            {
                "knowledge_id": "knowledge_private",
                "allowed_scopes": ["personal"],
                "max_sensitivity": "private",
            }
        )["status"]
        == "resolved"
    )
    assert (
        resolver.resolve({"knowledge_id": "knowledge_stale", "allowed_freshness": ["fresh"]})[
            "status"
        ]
        == "stale"
    )
    opaque = resolver.resolve({"authoritative_segment": {"receipt": "opaque"}})
    assert opaque["status"] == "index_unavailable"
    assert opaque["receipt"]["legal_authority"] is False
    assert (
        resolver.resolve({"authoritative_segment": {"receipt": "opaque", "payload": "secret"}})[
            "status"
        ]
        == "invalid"
    )
    assert resolver.resolve({"statement_target": "statement_1"})["status"] == "index_unavailable"


def test_admission_filters_candidates_before_provider_visible_ambiguity() -> None:
    resolver = _resolver(
        _page("knowledge_public", "public.md", aliases=["shared"]),
        _page(
            "knowledge_private",
            "private.md",
            aliases=["shared"],
            scope="personal",
            sensitivity="restricted",
        ),
    )
    mixed = resolver.resolve({"alias": "shared"})
    assert mixed["status"] == "resolved"
    assert mixed["candidate_count"] == 1
    assert [candidate["page_id"] for candidate in mixed["candidates"]] == ["knowledge_public"]
    assert "knowledge_private" not in json.dumps(mixed, sort_keys=True)
    assert mixed["receipt"]["suppressed_candidates"]["present"] is True

    all_denied = _resolver(
        _page(
            "knowledge_private_a",
            "private-a.md",
            aliases=["denied"],
            scope="personal",
            sensitivity="private",
        ),
        _page(
            "knowledge_private_b",
            "private-b.md",
            aliases=["denied"],
            scope="personal",
            sensitivity="restricted",
        ),
    ).resolve({"alias": "denied"})
    assert all_denied["status"] == "not_admitted"
    assert all_denied["candidate_count"] == 0
    assert all_denied["candidates"] == []
    assert all_denied["ambiguity"] is None
    assert "knowledge_private_a" not in json.dumps(all_denied["receipt"], sort_keys=True)
    assert "knowledge_private_b" not in json.dumps(all_denied["receipt"], sort_keys=True)
    schema = json.loads(Path("contracts/living-wiki-resolver.v1.schema.json").read_text())
    Draft202012Validator(schema).validate(mixed)
    Draft202012Validator(schema).validate(all_denied)


def test_resolver_component_is_bound_to_registry() -> None:
    page = _page("knowledge_one", "one.md")
    registry = build_page_registry(
        [page],
        v2_file_inventory=[
            {"path": "one.md", "byte_size": len(b"plain"), "sha256": sha256_bytes(b"plain")}
        ],
        input_audit_head=ZERO,
        legacy_audit_head=ZERO,
        v2_manifest_sha256=ZERO,
        generated_at="2026-08-08T00:00:00Z",
    )
    artifact = build_resolver_index(registry)
    assert artifact["component"]["registry_sha256"] == registry["registry_sha256"]
    assert artifact["component"]["index_sha256"] == artifact["index_sha256"]


def test_resolver_response_is_closed_contract() -> None:
    resolver = _resolver(_page("knowledge_one", "one.md"))
    response = resolver.resolve({"knowledge_id": "knowledge_one"})
    schema = json.loads(Path("contracts/living-wiki-resolver.v1.schema.json").read_text())
    Draft202012Validator(schema).validate(response)


def test_resolver_query_shape_is_closed_and_canonical() -> None:
    resolver = _resolver(_page("knowledge_one", "one.md"))
    assert resolver.resolve({"unknown": "value"})["status"] == "invalid"
    assert (
        resolver.resolve({"knowledge_id": "knowledge_one", "wiki_path": "one.md"})["status"]
        == "invalid"
    )
    assert resolver.resolve({"knowledge_id": "  knowledge_one  "})["status"] == "resolved"
    assert resolver.resolve({"knowledge_id": " \t "})["status"] == "invalid"
    assert resolver.resolve({"knowledge_id": "knowledge\x00one"})["status"] == "invalid"
