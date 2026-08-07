from __future__ import annotations

import copy
import json
import os
from pathlib import Path

import pytest
from jsonschema import Draft202012Validator, FormatChecker

from deeplaw.util import canonical_json, sha256_bytes
from deeplaw.wiki import (
    RegistryError,
    build_living_wiki_manifest_v3,
    build_page_registry,
    build_resolver_index,
    load_link_index,
    load_page_registry,
    load_resolver,
    validate_living_wiki_manifest_v3,
    validate_page_registry_component,
)
from deeplaw.wiki.registry import _canonical_digest, _safe_read_file

ZERO = "0" * 64


def _page(page_id: str, path: str, content: bytes = b"# page\n", **extra: object) -> dict:
    return {
        "page_id": page_id,
        "namespace": "knowledge",
        "canonical_page_path": path,
        "kind": "concept",
        "revision_id": f"revision_{page_id}",
        "audit_head": ZERO,
        "byte_size": len(content),
        "sha256": sha256_bytes(content),
        "scope": "project",
        "sensitivity": "public",
        "lifecycle": "active",
        "freshness": "fresh",
        "authority": "none",
        "input_refs": [f"input_{page_id}"],
        **extra,
    }


def _registry(*pages: dict) -> dict:
    inventory = [
        {
            "path": page["canonical_page_path"],
            "byte_size": page["byte_size"],
            "sha256": page["sha256"],
        }
        for page in pages
    ]
    return build_page_registry(
        pages,
        v2_file_inventory=inventory,
        input_audit_head=ZERO,
        legacy_audit_head=ZERO,
        v2_manifest_sha256=ZERO,
        generated_at="2026-08-08T00:00:00Z",
    )


def _materialize(root: Path, *artifacts: dict) -> None:
    for artifact in artifacts:
        for relative, payload in artifact["payloads"].items():
            target = root.joinpath(*relative.split("/"))
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(bytes(payload))


def _validate_definition(schema: dict, name: str, value: object) -> None:
    Draft202012Validator(
        {"$schema": schema["$schema"], "$defs": schema["$defs"], "$ref": f"#/$defs/{name}"},
        format_checker=FormatChecker(),
    ).validate(value)


def test_all_v3_schemas_are_valid_draft_202012_documents() -> None:
    for name in (
        "living-wiki-manifest.v3.schema.json",
        "living-wiki-page-registry.v1.schema.json",
        "living-wiki-link-index.v1.schema.json",
        "living-wiki-resolver.v1.schema.json",
    ):
        schema = json.loads(Path("contracts", name).read_text())
        Draft202012Validator.check_schema(schema)


def test_registry_is_deterministic_and_registers_zero_link_pages() -> None:
    one = _page("knowledge_a", "a.md", b"plain")
    two = _page("knowledge_b", "b.md", b"no links")
    left = _registry(two, one)
    right = _registry(one, two)
    assert left["registry_sha256"] == right["registry_sha256"]
    assert left["component"]["page_count"] == 2
    assert left["component"]["shards"][0]["record_count"] == 2
    Draft202012Validator(
        json.loads(Path("contracts/living-wiki-page-registry.v1.schema.json").read_text()),
        format_checker=FormatChecker(),
    ).validate(left["component"])
    schema = json.loads(Path("contracts/living-wiki-page-registry.v1.schema.json").read_text())
    for relative, payload in left["payloads"].items():
        if relative.endswith("/manifest.json"):
            continue
        _validate_definition(schema, "shard_document", json.loads(payload))


def test_path_or_title_never_grants_identity() -> None:
    page = _page("knowledge_explicit", "title-like.md", title="another-id")
    assert _registry(page)["records"][0]["page_id"] == "knowledge_explicit"
    with pytest.raises(RegistryError):
        _registry({**page, "page_id": ""})


def test_human_text_fields_reject_blank_and_control_values() -> None:
    page = _page("knowledge_explicit", "title-like.md")
    cases = (
        ("title", "\n"),
        ("semantic_key", " \t "),
        ("aliases", ["ok", "bad\x00"]),
    )
    for field, value in cases:
        with pytest.raises(RegistryError):
            _registry({**page, field: value})


def test_stable_identity_and_input_refs_use_closed_canonical_forms() -> None:
    page = _page("knowledge_explicit", "title-like.md")
    for field, value in (
        ("knowledge_id", "human readable"),
        ("projection_id", "éclair"),
        ("input_refs", [" input_ref "]),
        ("input_refs", ["bad\nref"]),
        ("input_refs", ["\x00"]),
    ):
        with pytest.raises(RegistryError):
            _registry({**page, field: value})
    normalized = _registry({**page, "input_refs": ["ref_b", "ref_a", "ref_b"]})
    assert normalized["records"][0]["input_refs"] == ["ref_a", "ref_b"]


def test_source_fragment_anchors_are_sorted_and_bounded() -> None:
    page = _page(
        "knowledge_anchor",
        "anchor.md",
        anchors=[
            {
                "anchor_id": "fragment_b",
                "anchor": "fragment:fragment_b",
                "kind": "source_fragment",
                "source_fragment": {
                    "source_revision_id": "source_rev",
                    "fragment_id": "fragment_b",
                },
            },
            {
                "anchor_id": "fragment_a",
                "anchor": "fragment:fragment_a",
                "kind": "source_fragment",
                "source_fragment": {
                    "source_revision_id": "source_rev",
                    "fragment_id": "fragment_a",
                },
            },
        ],
    )
    registry = _registry(page)
    assert [anchor["anchor_id"] for anchor in registry["records"][0]["anchors"]] == [
        "fragment_a",
        "fragment_b",
    ]
    with pytest.raises(RegistryError):
        _registry(
            {
                **page,
                "anchors": [
                    {
                        "anchor_id": "fragment_a",
                        "anchor": "fragment:fragment_a",
                        "kind": "source_fragment",
                        "source_fragment": {
                            "source_revision_id": "source_rev",
                            "fragment_id": "fragment_a",
                        },
                    }
                ]
                * 257,
            }
        )


def test_public_record_limit_is_enforced_before_materialization() -> None:
    with pytest.raises(RegistryError):
        build_page_registry(
            [{}] * 200_001,
            v2_file_inventory=[],
            input_audit_head=ZERO,
            legacy_audit_head=ZERO,
            v2_manifest_sha256=ZERO,
            generated_at="2026-08-08T00:00:00Z",
        )


def test_duplicate_page_identity_and_path_fail_closed() -> None:
    one = _page("knowledge_a", "a.md")
    with pytest.raises(RegistryError):
        _registry(one, {**_page("knowledge_b", "b.md"), "page_id": one["page_id"]})
    with pytest.raises(RegistryError):
        _registry(one, {**_page("knowledge_b", "b.md"), "canonical_page_path": "a.md"})


def test_shards_and_manifest_bind_components_and_reject_tamper() -> None:
    registry = _registry(_page("knowledge_a", "a.md"))
    resolver = {
        "component": {
            "schema_version": "deeplaw.living-wiki-resolver/v1",
            "registry_sha256": registry["registry_sha256"],
            "v2_manifest_sha256": ZERO,
            "input_audit_head": ZERO,
            "legacy_audit_head": ZERO,
            "generated_at": "2026-08-08T00:00:00Z",
            "candidate_count": 0,
            "candidate_ids_sha256": ZERO,
            "index_sha256": ZERO,
        }
    }
    # The standalone resolver/link modules provide real component digests; this test only asserts
    # that the manifest validator does not permit an overlap or a self-digest mutation.
    from deeplaw.wiki import build_link_index, build_resolver_index

    links = build_link_index(registry, {"a.md": b"# page\n"})
    resolver = build_resolver_index(registry)
    manifest = build_living_wiki_manifest_v3(
        input_audit_head=ZERO,
        legacy_audit_head=ZERO,
        generated_at="2026-08-08T00:00:00Z",
        v2_manifest_sha256=ZERO,
        page_registry=registry,
        link_index=links,
        resolver=resolver,
    )["manifest"]
    Draft202012Validator(
        json.loads(Path("contracts/living-wiki-manifest.v3.schema.json").read_text()),
        format_checker=FormatChecker(),
    ).validate(manifest)
    validate_living_wiki_manifest_v3(manifest)
    assert all("shards" not in component for component in manifest["components"])
    link_descriptor = next(
        row for row in manifest["components"] if row["component"] == "link_index"
    )
    assert link_descriptor["record_count"] == links["component"]["edge_count"]
    tampered = {**manifest, "v2_manifest_sha256": "1" * 64}
    with pytest.raises(RegistryError):
        validate_living_wiki_manifest_v3(tampered)


def test_component_payload_hash_and_sort_tamper_fail_closed() -> None:
    registry = _registry(_page("knowledge_a", "a.md"))
    shard = registry["component"]["shards"][0]
    bad = {**registry["component"], "shards": [{**shard, "sha256": "1" * 64}]}
    bad["registry_sha256"] = sha256_bytes(
        canonical_json({key: bad[key] for key in bad if key != "registry_sha256"}).encode()
    )
    with pytest.raises(RegistryError):
        validate_page_registry_component(bad, payloads=registry["payloads"])


def test_v3_manifest_uses_component_specific_record_limits() -> None:
    registry = _registry(_page("knowledge_a", "a.md"))
    from deeplaw.wiki import build_link_index

    links = build_link_index(registry, {"a.md": b"# page\n"})
    resolver = build_resolver_index(registry)
    manifest = build_living_wiki_manifest_v3(
        input_audit_head=ZERO,
        legacy_audit_head=ZERO,
        generated_at="2026-08-08T00:00:00Z",
        v2_manifest_sha256=ZERO,
        page_registry=registry,
        link_index=links,
        resolver=resolver,
    )["manifest"]
    for component_name, value in (("page_registry", 200_001), ("link_index", 2_000_001)):
        tampered = copy.deepcopy(manifest)
        component = next(
            row for row in tampered["components"] if row["component"] == component_name
        )
        component["record_count"] = value
        tampered["manifest_sha256"] = _canonical_digest(
            {key: tampered[key] for key in tampered if key != "manifest_sha256"}
        )
        with pytest.raises(RegistryError):
            validate_living_wiki_manifest_v3(tampered)


def test_filesystem_loader_rejects_symlinked_manifests(tmp_path: Path) -> None:
    page = _page("knowledge_a", "a.md")
    registry = _registry(page)
    from deeplaw.wiki import build_link_index

    links = build_link_index(registry, {"a.md": b"# page\n"})
    resolver = build_resolver_index(registry)
    manifest = build_living_wiki_manifest_v3(
        input_audit_head=ZERO,
        legacy_audit_head=ZERO,
        generated_at="2026-08-08T00:00:00Z",
        v2_manifest_sha256=ZERO,
        page_registry=registry,
        link_index=links,
        resolver=resolver,
    )["manifest"]
    _materialize(tmp_path, registry, links, resolver)
    outside = tmp_path.parent / "deeplaw-wiki-loader-outside.json"
    outside.write_bytes(b"not a registered artifact")
    cases = [
        (registry["manifest_path"], lambda: load_page_registry(tmp_path, manifest)),
        (
            registry["component"]["shards"][0]["path"],
            lambda: load_page_registry(tmp_path, manifest),
        ),
        (links["manifest_path"], lambda: load_link_index(tmp_path, manifest, registry)),
        (
            links["component"]["coverage_shards"][0]["path"],
            lambda: load_link_index(tmp_path, manifest, registry),
        ),
        (resolver["manifest_path"], lambda: load_resolver(tmp_path, manifest, registry)),
    ]
    for relative, loader in cases:
        target = tmp_path.joinpath(*relative.split("/"))
        target.unlink()
        os.symlink(outside, target)
        with pytest.raises(RegistryError):
            loader()
        target.unlink()
        target.write_bytes(
            next(
                bytes(artifact["payloads"][relative])
                for artifact in (registry, links, resolver)
                if relative in artifact["payloads"]
            )
        )


def test_safe_reader_rejects_intermediate_symlink_and_nonregular_target(tmp_path: Path) -> None:
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "artifact.json").write_bytes(b"outside")
    root = tmp_path / "root"
    root.mkdir()
    (root / "link").symlink_to(outside, target_is_directory=True)
    with pytest.raises(RegistryError):
        _safe_read_file(root, "link/artifact.json", max_bytes=1024, field="test")

    (root / "directory").mkdir()
    with pytest.raises(RegistryError):
        _safe_read_file(root, "directory", max_bytes=1024, field="test")
