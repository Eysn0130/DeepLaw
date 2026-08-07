from __future__ import annotations

import hashlib
import json
from pathlib import Path

from jsonschema import Draft202012Validator, FormatChecker

from deeplaw import knowledge_autonomy
from deeplaw.compilation.coordinator import CompilationCoordinator
from deeplaw.knowledge_autonomy import (
    SINK_OPERATIONS,
    AutonomousKnowledgeStore,
    initialize_autonomous_core,
)
from deeplaw.knowledge_store import initialize_knowledge_vault
from deeplaw.util import canonical_json


def _vault(tmp_path: Path) -> Path:
    root = tmp_path / "vault"
    initialize_knowledge_vault(root, name="v013-ownership", scope="project")
    initialize_autonomous_core(root)
    return root


def _grant(store: AutonomousKnowledgeStore) -> str:
    return store.enable_grant(
        writer_id="v013-ownership-tests",
        operations=tuple(sorted(SINK_OPERATIONS)),
        max_mutations_per_minute=120,
    )["grant_id"]


def _rebuild(tmp_path: Path) -> tuple[Path, dict[str, object]]:
    root = _vault(tmp_path)
    with AutonomousKnowledgeStore(root, read_only=False) as store:
        grant_id = _grant(store)
        store.remember(
            grant_id=grant_id,
            idempotency_key="projection-ownership",
            title="Projection ownership",
            body="A deterministic object for aggregate manifest ownership.",
            semantic_key="v013.projection.ownership",
            confirm_no_case_data=True,
        )
        rebuilt = store.rebuild_derived()
    return root, rebuilt


def _read_json(path: Path) -> dict[str, object]:
    return json.loads(path.read_text(encoding="utf-8"))


def _write_json(path: Path, value: dict[str, object]) -> None:
    path.write_text(
        json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def _digest_manifest(value: dict[str, object]) -> str:
    body = {key: item for key, item in value.items() if key != "manifest_sha256"}
    return hashlib.sha256(canonical_json(body).encode("utf-8")).hexdigest()


def test_v2_manifest_has_unique_direct_and_living_wiki_ownership(tmp_path: Path) -> None:
    root, rebuilt = _rebuild(tmp_path)
    top = _read_json(root / ".deeplaw" / "derived" / "manifest.json")
    living = _read_json(root / ".deeplaw" / "derived" / "tree" / "living-wiki-manifest.json")

    assert top["schema_version"] == "deeplaw.derived-manifest/v2"
    direct = {item["path"] for item in top["files"]}
    component = top["components"][0]
    owned = {item["path"] for item in living["files"]}
    assert direct == {
        ".deeplaw/derived/vectors/vectors.bin",
        ".deeplaw/derived/vectors/records.json",
        ".deeplaw/derived/vectors/manifest.json",
    }
    assert direct.isdisjoint(owned)
    assert component["file_count"] == len(owned)
    assert rebuilt["files"] == top["files"]
    assert rebuilt["components"] == top["components"]

    receipt = CompilationCoordinator._projection_receipt(rebuilt)
    assert receipt["derived_file_count"] == len(direct) + len(owned)
    assert receipt["derived_file_inventory_sha256"] == hashlib.sha256(
        canonical_json({"direct_files": top["files"], "components": top["components"]}).encode(
            "utf-8"
        )
    ).hexdigest()


def test_v2_component_file_tamper_is_a_verification_failure(tmp_path: Path) -> None:
    root, _ = _rebuild(tmp_path)
    top = _read_json(root / ".deeplaw" / "derived" / "manifest.json")
    living = _read_json(root / ".deeplaw" / "derived" / "tree" / "living-wiki-manifest.json")
    target = root / living["files"][0]["path"]
    target.write_bytes(target.read_bytes() + b"tampered")

    with AutonomousKnowledgeStore(root, read_only=True) as store:
        verification = store.verify()
    assert verification["valid"] is False
    assert verification["derived_ready"] is False
    assert {item["code"] for item in verification["failures"]} >= {"derived_manifest_invalid"}
    assert "derived_manifest_stale" not in {item["code"] for item in verification["warnings"]}
    assert top["schema_version"] == "deeplaw.derived-manifest/v2"


def test_v2_component_manifest_tamper_is_a_verification_failure(tmp_path: Path) -> None:
    root, _ = _rebuild(tmp_path)
    manifest_path = root / ".deeplaw" / "derived" / "tree" / "living-wiki-manifest.json"
    living = _read_json(manifest_path)
    living["configuration_sha256"] = "0" * 64
    _write_json(manifest_path, living)

    with AutonomousKnowledgeStore(root, read_only=True) as store:
        verification = store.verify()
    assert verification["valid"] is False
    assert verification["derived_ready"] is False
    assert {item["code"] for item in verification["failures"]} >= {"derived_manifest_invalid"}
    assert not verification["warnings"] or "derived_manifest_stale" not in {
        item["code"] for item in verification["warnings"]
    }


def test_v2_recomputed_top_hash_cannot_hide_direct_component_overlap(tmp_path: Path) -> None:
    root, _ = _rebuild(tmp_path)
    top_path = root / ".deeplaw" / "derived" / "manifest.json"
    top = _read_json(top_path)
    living_path = root / ".deeplaw" / "derived" / "tree" / "living-wiki-manifest.json"
    living = _read_json(living_path)
    direct_item = next(
        item for item in top["files"] if item["path"].endswith("/records.json")
    )
    living["files"][0] = {
        "path": direct_item["path"],
        "byte_size": direct_item["byte_size"],
        "sha256": direct_item["sha256"],
    }
    living["files"] = sorted(living["files"], key=lambda item: item["path"])
    living["manifest_sha256"] = _digest_manifest(living)
    _write_json(living_path, living)
    component = top["components"][0]
    component["manifest_sha256"] = living["manifest_sha256"]
    component["manifest_byte_size"] = living_path.stat().st_size
    component["file_inventory_sha256"] = hashlib.sha256(
        canonical_json(living["files"]).encode("utf-8")
    ).hexdigest()
    top["manifest_sha256"] = _digest_manifest(top)
    _write_json(top_path, top)

    with AutonomousKnowledgeStore(root, read_only=True) as store:
        verification = store.verify()
    assert verification["valid"] is False
    assert verification["derived_ready"] is False
    assert {item["code"] for item in verification["failures"]} >= {"derived_manifest_invalid"}


def test_v1_aggregate_manifest_remains_accepted(tmp_path: Path) -> None:
    root, _ = _rebuild(tmp_path)
    top_path = root / ".deeplaw" / "derived" / "manifest.json"
    top = _read_json(top_path)
    living = _read_json(root / ".deeplaw" / "derived" / "tree" / "living-wiki-manifest.json")
    legacy = {
        key: value
        for key, value in top.items()
        if key not in {"schema_version", "components", "manifest_sha256"}
    }
    legacy["schema_version"] = "deeplaw.derived-manifest/v1"
    legacy["files"] = sorted(
        [*top["files"], *living["files"]],
        key=lambda item: item["path"],
    )
    legacy["manifest_sha256"] = _digest_manifest(legacy)
    _write_json(top_path, legacy)

    with AutonomousKnowledgeStore(root, read_only=True) as store:
        verification = store.verify()
    assert verification["valid"] is True, verification["failures"]
    assert verification["derived_ready"] is True


def test_v2_temporal_expiry_is_a_stale_projection_warning(tmp_path: Path, monkeypatch) -> None:
    root = _vault(tmp_path)
    with AutonomousKnowledgeStore(root, read_only=False) as store:
        grant_id = _grant(store)
        store.remember(
            grant_id=grant_id,
            idempotency_key="temporal-expiry",
            title="Temporal projection",
            body="This object expires after the projection event.",
            valid_to="2999-01-01T00:00:00Z",
            confirm_no_case_data=True,
        )
        store.rebuild_derived()

    monkeypatch.setattr(knowledge_autonomy, "utc_now", lambda: "2999-02-01T00:00:00Z")
    with AutonomousKnowledgeStore(root, read_only=True) as store:
        verification = store.verify()
    assert verification["valid"] is True
    assert verification["derived_ready"] is False
    assert {item["code"] for item in verification["warnings"]} >= {
        "derived_manifest_stale"
    }


def test_v2_schema_has_draft_2020_and_separate_component_capacity() -> None:
    schema = json.loads(
        (Path(__file__).parents[1] / "contracts" / "derived-manifest.v2.schema.json").read_text(
            encoding="utf-8"
        )
    )
    Draft202012Validator.check_schema(schema)
    Draft202012Validator(schema, format_checker=FormatChecker())
    assert schema["$schema"] == "https://json-schema.org/draft/2020-12/schema"
    assert schema["properties"]["files"]["maxItems"] == 3
    assert (
        schema["properties"]["components"]["items"]["properties"]["file_count"]["maximum"]
        >= 100_000
    )
