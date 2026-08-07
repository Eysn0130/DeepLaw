from __future__ import annotations

import ast
import copy
import json
from pathlib import Path

import pytest
from jsonschema import Draft202012Validator

from deeplaw.authoritative_navigator import (
    build_authoritative_navigator,
    derive_review_dispositions,
    verify_authoritative_navigator,
    verify_review_dispositions,
)
from deeplaw.util import canonical_json, sha256_bytes

ROOT = Path(__file__).resolve().parents[1]
MATRIX_PATH = ROOT / "benchmarks" / "quality" / "v0.11-28-source-decision-matrix.json"


def _fixture() -> tuple[dict, dict, list[dict], list[dict]]:
    digest = "0" * 64
    pack = {
        "pack_id": "authpack_" + "a" * 24,
        "catalog_id": "deeplaw-cn-official",
        "catalog_sha256": digest,
        "catalog_sequence": 2,
    }
    release = {
        "release_id": "lawrel_" + "b" * 32,
        "release_sha256": digest,
        "version": "0.13.0-test",
        "published_on": "2026-01-01",
    }
    documents = [
        {
            "document_id": "doc_" + "c" * 24,
            "source_sha256": digest,
            "title": "Document A",
            "effective_from": "2020-01-01",
            "effective_to": "2025-12-31",
            "capability": "exact_segment",
        },
        {
            "document_id": "doc_" + "d" * 24,
            "source_sha256": digest,
            "title": "Document B",
        },
    ]
    segments = [
        {
            "segment_id": "seg_" + "e" * 24,
            "document_id": documents[0]["document_id"],
            "source_sha256": digest,
            "segment_sha256": digest,
            "receipt_id": "lawrcpt_" + "f" * 32,
            "ordinal": 1,
            "article_label": "A-1",
            "effective_from": "2020-01-01",
            "effective_to": "2025-12-31",
            "capability": "exact_segment",
        },
        {
            "segment_id": "seg_" + "1" * 24,
            "document_id": documents[1]["document_id"],
            "source_sha256": digest,
            "segment_sha256": digest,
            "receipt_id": "lawrcpt_" + "2" * 32,
            "ordinal": 1,
            # The missing capability is intentionally fail-closed.
        },
    ]
    return pack, release, documents, segments


def _navigator(**overrides: object) -> dict:
    pack, release, documents, segments = _fixture()
    values = {
        "pack": pack,
        "release": release,
        "documents": documents,
        "segments": segments,
        "definitions": [
            {
                "definition_id": "def_a",
                "segment_id": segments[0]["segment_id"],
                "label": "bounded-label",
            }
        ],
        "cross_references": [
            {
                "cross_reference_id": "xref_a",
                "from_segment_id": segments[0]["segment_id"],
                "to_segment_id": segments[1]["segment_id"],
                "label": "bounded-reference",
            }
        ],
        "review_warnings": [
            {
                "warning_id": "warning_a",
                "segment_id": segments[0]["segment_id"],
                "label": "review-required",
                "warning_count": 1,
            }
        ],
    }
    values.update(overrides)
    return build_authoritative_navigator(**values)


def test_public_matrix_has_exact_five_sources_32_warnings_and_8_segments() -> None:
    matrix = json.loads(MATRIX_PATH.read_text(encoding="utf-8"))
    value = derive_review_dispositions(matrix)
    assert value["source_count"] == 5
    assert value["warning_count"] == 32
    assert value["review_required_segments"] == 8
    assert {item["source_id"] for item in value["dispositions"]} == {
        "doc_003bce0e629646f4798dad04",
        "doc_27744b8e4a30bea1d9e3f92f",
        "doc_60224e01894c870874c413df",
        "doc_9364963e345975e871203e53",
        "doc_d63068d170e2015069276833",
    }
    assert all(item["status"] == "maintainer_review_pending" for item in value["dispositions"])
    assert all(item["expert_status"] == "expert_review_pending" for item in value["dispositions"])
    assert all(
        item["capability_after"] == "identity_locator_only" for item in value["dispositions"]
    )
    assert verify_review_dispositions(value)["valid"] is True


def test_matrix_tamper_fails_closed() -> None:
    matrix = json.loads(MATRIX_PATH.read_text(encoding="utf-8"))
    tampered = copy.deepcopy(matrix)
    tampered["sources"][0]["extraction_quality"]["warning_count"] += 1
    with pytest.raises(ValueError):
        derive_review_dispositions(tampered)
    tampered = copy.deepcopy(matrix)
    tampered["record_sha256"] = "f" * 64
    with pytest.raises(ValueError):
        derive_review_dispositions(tampered)


def test_navigator_has_all_sections_and_is_order_independent() -> None:
    first = _navigator()
    second = _navigator(
        documents=list(reversed(_fixture()[2])),
        segments=list(reversed(_fixture()[3])),
    )
    assert first["navigator_sha256"] == second["navigator_sha256"]
    assert set(first) == {
        "schema_version",
        "derived_view",
        "read_only",
        "official_prose_generated",
        "authority_changed",
        "legal_authority_decision",
        "binding",
        "document_index",
        "release_timeline",
        "effective_dates",
        "segment_index",
        "definitions",
        "cross_references",
        "review_warnings",
        "evidence_gaps",
        "receipt_drill_down",
        "manifest",
        "navigator_sha256",
    }
    assert first["derived_view"] is True
    assert first["read_only"] is True
    assert first["official_prose_generated"] is False
    assert first["authority_changed"] is False
    assert first["legal_authority_decision"] is False
    assert first["receipt_drill_down"]
    assert first["cross_references"][0]["to_segment_id"] in {
        segment["segment_id"] for segment in first["segment_index"]
    }
    assert verify_authoritative_navigator(first)["valid"] is True


def test_navigator_schema_and_capability_downgrade() -> None:
    value = _navigator()
    schema = json.loads(
        (ROOT / "contracts" / "authoritative-navigator.v1.schema.json").read_text(encoding="utf-8")
    )
    errors = list(Draft202012Validator(schema).iter_errors(value))
    assert errors == []
    assert value["segment_index"][1]["capability"] == "identity_locator_only"
    assert value["document_index"][1]["capability"] == "identity_locator_only"


def test_navigator_tamper_unknown_fields_hashes_and_dates_fail() -> None:
    value = _navigator()
    tampered = copy.deepcopy(value)
    tampered["segment_index"][0]["segment_sha256"] = "1" * 64
    assert verify_authoritative_navigator(tampered)["valid"] is False
    tampered = copy.deepcopy(value)
    tampered["segment_index"][0]["effective_from"] = "2026-01-01"
    tampered["segment_index"][0]["effective_to"] = "2025-01-01"
    assert verify_authoritative_navigator(tampered)["valid"] is False
    tampered = copy.deepcopy(value)
    tampered["segment_index"][0]["body"] = "unbounded source prose"
    assert verify_authoritative_navigator(tampered)["valid"] is False
    tampered = copy.deepcopy(value)
    tampered["navigator_sha256"] = "f" * 64
    assert verify_authoritative_navigator(tampered)["valid"] is False


def test_navigator_security_rejects_paths_control_and_unknown_capability() -> None:
    with pytest.raises(ValueError):
        _navigator(documents=[{**_fixture()[2][0], "title": "/Users/private/source.pdf"}])
    with pytest.raises(ValueError):
        _navigator(documents=[{**_fixture()[2][0], "title": "bad\nlabel"}])
    with pytest.raises(ValueError):
        _navigator(segments=[{**_fixture()[3][0], "capability": "invented_capability"}])
    with pytest.raises(ValueError):
        _navigator(documents=[{**_fixture()[2][0], "unknown_field": "value"}])


def test_structured_capability_requires_complete_authoritative_artifact() -> None:
    pack, release, documents, segments = _fixture()
    capability = {
        "schema_version": "deeplaw.evidence-capabilities/v1",
        "integrity": "verified",
        "source_identity": "signed_official",
        "authority_metadata": "verified",
        "temporal": "verified_at",
        "extraction": "native_reviewed",
        "provenance": "exact_segment",
        "temporal_as_of": "2025-01-01",
    }
    capability["capability_sha256"] = sha256_bytes(canonical_json(capability).encode("utf-8"))
    documents[0]["capabilities"] = capability
    segments[0]["capabilities"] = capability
    value = build_authoritative_navigator(pack, release, documents, segments)
    assert value["document_index"][0]["capability"] == "exact_segment"
    assert value["segment_index"][0]["capability"] == "exact_segment"

    warned = copy.deepcopy(capability)
    warned["extraction"] = "warned"
    warned_body = dict(warned)
    warned_body.pop("capability_sha256")
    warned["capability_sha256"] = sha256_bytes(canonical_json(warned_body).encode("utf-8"))
    documents[0]["capabilities"] = warned
    segments[0]["capabilities"] = warned
    value = build_authoritative_navigator(pack, release, documents, segments)
    assert value["document_index"][0]["capability"] == "identity_locator_only"
    assert value["segment_index"][0]["capability"] == "identity_locator_only"

    partial = {"schema_version": "deeplaw.evidence-capabilities/v1", "integrity": "verified"}
    documents[0]["capabilities"] = partial
    segments[0]["capabilities"] = partial
    value = build_authoritative_navigator(pack, release, documents, segments)
    assert value["document_index"][0]["capability"] == "identity_locator_only"
    assert value["segment_index"][0]["capability"] == "identity_locator_only"

    bad_digest = copy.deepcopy(capability)
    bad_digest["capability_sha256"] = "f" * 64
    with pytest.raises(ValueError):
        build_authoritative_navigator(
            pack,
            release,
            [{**documents[0], "capabilities": bad_digest}, documents[1]],
            segments,
        )


def test_disposition_schema_and_flags_are_closed() -> None:
    matrix = json.loads(MATRIX_PATH.read_text(encoding="utf-8"))
    value = derive_review_dispositions(matrix)
    schema = json.loads(
        (ROOT / "contracts" / "authoritative-review-disposition.v1.schema.json").read_text(
            encoding="utf-8"
        )
    )
    assert list(Draft202012Validator(schema).iter_errors(value)) == []
    tampered = copy.deepcopy(value)
    tampered["dispositions"][0]["human_reviewed"] = True
    assert verify_review_dispositions(tampered)["valid"] is False


def test_product_module_has_no_benchmark_or_mutation_or_network_imports() -> None:
    tree = ast.parse(
        (ROOT / "src" / "deeplaw" / "authoritative_navigator.py").read_text(encoding="utf-8")
    )
    imported: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported.extend(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            imported.append(node.module or "")
    forbidden = ("benchmark", "tests", "sqlite", "requests", "urllib", "http", "model")
    assert not any(any(term in name.lower() for term in forbidden) for name in imported)
