from __future__ import annotations

import copy
import json
from pathlib import Path

import pytest
from jsonschema import Draft202012Validator, FormatChecker

from deeplaw.util import sha256_bytes
from deeplaw.wiki import RegistryError, build_link_index, build_page_registry, query_links
from deeplaw.wiki import link_index as link_index_module

ZERO = "0" * 64


def _page(page_id: str, path: str, body: bytes, **extra: object) -> dict:
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


def _build(pages: list[dict]) -> tuple[dict, dict[str, bytes]]:
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
    return registry, {
        page["canonical_page_path"]: body
        for page, body in zip(
            pages,
            (b"[[target.md]]" if page["page_id"] == "source" else b"plain" for page in pages),
            strict=True,
        )
    }


def _validate_definition(schema: dict, name: str, value: object) -> None:
    Draft202012Validator(
        {"$schema": schema["$schema"], "$defs": schema["$defs"], "$ref": f"#/$defs/{name}"},
        format_checker=FormatChecker(),
    ).validate(value)


def test_zero_link_coverage_and_exact_backlink_pagination() -> None:
    source_body = b"[[target.md]]"
    target_body = b"plain"
    pages = [_page("source", "source.md", source_body), _page("target", "target.md", target_body)]
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
    index = build_link_index(registry, {"source.md": source_body, "target.md": target_body})
    schema = json.loads(Path("contracts/living-wiki-link-index.v1.schema.json").read_text())
    Draft202012Validator(schema, format_checker=FormatChecker()).validate(index["component"])
    for relative, payload in index["payloads"].items():
        if relative.endswith("/manifest.json"):
            continue
        definition = "coverage_shard_document" if "/coverage/" in relative else "shard_document"
        _validate_definition(schema, definition, json.loads(payload))
    assert len(index["coverage"]) == 2
    assert next(row for row in index["coverage"] if row["page_id"] == "target")["link_count"] == 0
    result = query_links(index, "target", limit=1)
    assert result["index_used"] is True
    assert result["total_count"] == 1
    assert result["truncated"] is False


def test_many_backlinks_have_exact_count_and_cursor_binding() -> None:
    pages = [_page("target", "target.md", b"plain")]
    bodies = {"target.md": b"plain"}
    for index in range(1_002):
        page_id = f"source_{index:04d}"
        path = f"pages/{page_id}.md"
        body = b"[[target.md]]"
        pages.append(_page(page_id, path, body))
        bodies[path] = body
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
    index = build_link_index(registry, bodies)
    first = query_links(index, "target", limit=100)
    assert first["total_count"] == 1_002
    assert first["truncated"] is True
    seen = len(first["links"])
    cursor = first["cursor"]
    while cursor:
        page = query_links(index, "target", limit=100, cursor=cursor)
        seen += len(page["links"])
        cursor = page["cursor"]
    assert seen == 1_002


def test_cursor_is_bound_to_direction_and_limit() -> None:
    pages = [_page("target", "target.md", b"plain")]
    bodies = {"target.md": b"plain"}
    for index in range(2):
        page_id = f"source_{index}"
        path = f"pages/{page_id}.md"
        body = b"[[target.md]]"
        pages.append(_page(page_id, path, body))
        bodies[path] = body
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
    index = build_link_index(registry, bodies)
    first = query_links(index, "target", limit=1)
    assert first["cursor"]
    with pytest.raises(ValueError):
        query_links(index, "target", direction="outlinks", limit=1, cursor=first["cursor"])
    with pytest.raises(ValueError):
        query_links(index, "target", direction="backlinks", limit=2, cursor=first["cursor"])


def test_validated_handle_query_does_not_rescan_all_shards(monkeypatch: pytest.MonkeyPatch) -> None:
    pages = [
        _page("source", "source.md", b"[[target.md]]"),
        _page("target", "target.md", b"plain"),
    ]
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
    index = build_link_index(registry, {"source.md": b"[[target.md]]", "target.md": b"plain"})

    def fail_full_validation(*args: object, **kwargs: object) -> object:
        raise AssertionError("production handle query rescanned full index")

    monkeypatch.setattr(link_index_module, "validate_link_index_component", fail_full_validation)
    result = query_links(index, "target")
    assert result["status"] == "ok"
    assert result["index_used"] is True


def test_edge_identity_binds_page_revision_fragment_identity_and_audit_head() -> None:
    source_body = b"[[target.md]]"
    target_body = b"plain"
    source = _page("source", "source.md", source_body)
    target = _page("target", "target.md", target_body)
    pages = [source, target]
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
    index = build_link_index(registry, {"source.md": source_body, "target.md": target_body})
    edge = index["edges"][0]
    assert edge["source_page_revision"] == source["revision_id"]
    assert edge["audit_head"] == source["audit_head"]
    first_edge_id = edge["edge_id"]

    fragment = _page(
        "fragment",
        "fragment.md",
        source_body,
        source_fragment={"source_revision_id": "source_rev", "fragment_id": "fragment_1"},
    )
    fragment_registry = build_page_registry(
        [fragment, target],
        v2_file_inventory=[
            {
                "path": page["canonical_page_path"],
                "byte_size": page["byte_size"],
                "sha256": page["sha256"],
            }
            for page in [fragment, target]
        ],
        input_audit_head=ZERO,
        legacy_audit_head=ZERO,
        v2_manifest_sha256=ZERO,
        generated_at="2026-08-08T00:00:00Z",
    )
    fragment_index = build_link_index(
        fragment_registry, {"fragment.md": source_body, "target.md": target_body}
    )
    assert fragment_index["edges"][0]["source_page_revision"] == fragment["source_fragment"]

    changed = {**source, "audit_head": "1" * 64}
    changed_registry = build_page_registry(
        [changed, target],
        v2_file_inventory=[
            {
                "path": page["canonical_page_path"],
                "byte_size": page["byte_size"],
                "sha256": page["sha256"],
            }
            for page in [changed, target]
        ],
        input_audit_head=ZERO,
        legacy_audit_head=ZERO,
        v2_manifest_sha256=ZERO,
        generated_at="2026-08-08T00:00:00Z",
    )
    changed_index = build_link_index(
        changed_registry, {"source.md": source_body, "target.md": target_body}
    )
    assert changed_index["edges"][0]["edge_id"] != first_edge_id


def test_ambiguous_edge_records_total_candidates_and_explicit_truncation() -> None:
    source_body = b"[[target.md]]"
    target_body = b"plain"
    pages = [_page("source", "source.md", source_body), _page("target", "target.md", target_body)]
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

    class AmbiguousResolver:
        def resolve(self, query: object, *, limit: int) -> dict:
            assert limit == 2_000
            return {
                "status": "ambiguous",
                "candidate_count": 3,
                "candidates": [
                    {"page_id": "target", "reason": "alias"},
                ],
            }

    index = build_link_index(
        registry,
        {"source.md": source_body, "target.md": target_body},
        resolver=AmbiguousResolver(),
    )
    edge = index["edges"][0]
    assert edge["candidate_count"] == 3
    assert edge["candidates_truncated"] is True
    assert edge["truncation_reason"] == "candidate_limit"
    assert edge["target_page_ids"] == ["target"]


def test_link_edge_limit_is_independent_from_page_limit() -> None:
    pages = [_page("source", "source.md", b"plain")]
    registry = build_page_registry(
        pages,
        v2_file_inventory=[
            {"path": "source.md", "byte_size": len(b"plain"), "sha256": sha256_bytes(b"plain")}
        ],
        input_audit_head=ZERO,
        legacy_audit_head=ZERO,
        v2_manifest_sha256=ZERO,
        generated_at="2026-08-08T00:00:00Z",
    )
    index = build_link_index(registry, {"source.md": b"plain"})
    tampered = {**index["component"], "edge_count": 2_000_001}
    with pytest.raises(RegistryError):
        link_index_module.validate_link_index_component(tampered)


def test_scanner_excludes_fenced_inline_and_escaped_wikilinks() -> None:
    body = b"```\n[[fake_fenced.md]]\n```\n`[[fake_inline.md]]` \\[[fake_escaped.md]] [[real.md]]"
    source = _page("source", "source.md", body)
    real = _page("real", "real.md", b"plain")
    pages = [source, real]
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
    index = build_link_index(registry, {"source.md": body, "real.md": b"plain"})
    assert len(index["edges"]) == 1
    assert index["edges"][0]["target_raw"] == "real.md"


def test_tampered_edges_fail_closed_without_filesystem_scan() -> None:
    body = b"[[target.md]]"
    pages = [_page("source", "source.md", body), _page("target", "target.md", b"plain")]
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
    index = build_link_index(registry, {"source.md": body, "target.md": b"plain"})
    index["edges"][0]["target_page_ids"] = []
    index["valid"] = True
    result = query_links(index, "target")
    assert result["index_used"] is False
    assert result["gap"] == "living_wiki_link_index_invalid_or_stale"


def test_tampered_coverage_or_manifest_fails_closed() -> None:
    pages = [_page("source", "source.md", b"[[target.md]]"), _page("target", "target.md", b"plain")]
    registry, bodies = _build(pages)
    index = build_link_index(registry, bodies)
    tampered_coverage = copy.deepcopy(index)
    tampered_coverage["coverage"][0]["link_count"] += 1
    assert query_links(tampered_coverage, "target")["index_used"] is False
    tampered_manifest = copy.deepcopy(index)
    tampered_manifest["manifest_bytes"] = b"tampered"
    assert query_links(tampered_manifest, "target")["index_used"] is False


def test_zero_link_coverage_scales_without_inline_manifest_payload() -> None:
    # Ten thousand rows are sufficient to exceed the former inline-coverage 1 MiB bound while
    # keeping this regression test practical on a local checkout.  Sharding policy is fixed and
    # therefore covers the 100k-page case with the same bounded descriptor shape.
    pages = [_page("page_00000", "pages/page_00000.md", b"plain")]
    for index in range(1, 10_000):
        page_id = f"page_{index:05d}"
        pages.append(_page(page_id, f"pages/{page_id}.md", b"plain"))
    registry, bodies = _build(pages)
    index = build_link_index(registry, bodies)
    assert len(index["manifest_bytes"]) < 1 * 1024 * 1024
    assert index["component"]["coverage_record_count"] == 10_000
    assert "coverage" not in index["component"]
    assert all(shard["byte_size"] <= 256 * 1024 for shard in index["component"]["coverage_shards"])
