from __future__ import annotations

import json
from pathlib import Path

from deeplaw.knowledge_compiler import compile_source
from deeplaw.knowledge_store import KnowledgeVault, initialize_knowledge_vault
from deeplaw.skill_factory import (
    build_skill_bundle,
    install_skill_bundle,
    verify_skill_bundle,
)


def _reviewed_vault(tmp_path: Path) -> Path:
    root = tmp_path / "vault"
    initialize_knowledge_vault(root, name="skills", scope="project")
    source = tmp_path / "procedure.md"
    source.write_text(
        "# Release procedure\nVerify the signed artifact before deployment.\n",
        encoding="utf-8",
    )
    with KnowledgeVault(root, read_only=False) as vault:
        result = compile_source(
            vault,
            source,
            source_kind="document",
            confirm_no_case_data=True,
            typed_extraction="deterministic-v2",
        )
        source_id = result["source"]["source_id"]
        manifest = vault.source_review_manifest(source_id)
        vault.approve_source_assets(
            source_id,
            confirm_reviewed=True,
            confirm_quarantined=True,
            review_manifest_sha256=manifest["review_manifest_sha256"],
        )
    return root


def test_skill_bundle_is_revision_bound_deterministic_and_verifiable(
    tmp_path: Path,
) -> None:
    root = _reviewed_vault(tmp_path)
    first = tmp_path / "first"
    second = tmp_path / "second"
    with KnowledgeVault(root, read_only=True) as vault:
        one = build_skill_bundle(
            vault,
            first,
            skill_name="release-safety",
            description="Use reviewed release procedures with exact DeepLaw provenance.",
            targets=("codex", "claude-code", "opencode", "generic"),
        )
        two = build_skill_bundle(
            vault,
            second,
            skill_name="release-safety",
            description="Use reviewed release procedures with exact DeepLaw provenance.",
            targets=("codex", "claude-code", "opencode", "generic"),
        )
        verification = verify_skill_bundle(first, vault=vault)

    assert one["bundle_id"] == two["bundle_id"]
    assert one["manifest_sha256"] == two["manifest_sha256"]
    assert one["asset_revisions"]
    assert one["source_hashes"]
    assert verification["valid"] is True
    manifest = json.loads((first / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["read_only"] is True
    assert manifest["canonical_authority"] is False
    assert "approve" not in (first / "SKILL.md").read_text(encoding="utf-8").split(
        "## Reviewed knowledge index", 1
    )[1]


def test_external_skill_install_defaults_to_quarantine_and_updates_atomically(
    tmp_path: Path,
) -> None:
    root = _reviewed_vault(tmp_path)
    bundle = tmp_path / "bundle"
    with KnowledgeVault(root, read_only=True) as vault:
        built = build_skill_bundle(
            vault,
            bundle,
            skill_name="release-safety",
            description="Use reviewed release procedures.",
        )

    installed = install_skill_bundle(
        bundle,
        tmp_path / "skills",
        target="generic",
        expected_vault_id="vault_000000000000000000000000",
        confirm=True,
    )
    assert installed["quarantined"] is True
    destination = Path(installed["destination"])
    assert built["bundle_id"] in destination.parts
    assert verify_skill_bundle(destination)["valid"] is True

    updated = install_skill_bundle(
        bundle,
        tmp_path / "skills",
        target="generic",
        expected_vault_id="vault_000000000000000000000000",
        confirm=True,
        update=True,
    )
    assert updated["updated"] is True
    assert updated["valid"] is True


def test_skill_verifier_rejects_tampering(tmp_path: Path) -> None:
    root = _reviewed_vault(tmp_path)
    bundle = tmp_path / "bundle"
    with KnowledgeVault(root, read_only=True) as vault:
        build_skill_bundle(
            vault,
            bundle,
            skill_name="release-safety",
            description="Use reviewed release procedures.",
        )
    (bundle / "knowledge.json").write_text("{}\n", encoding="utf-8")

    verification = verify_skill_bundle(bundle)

    assert verification["valid"] is False
    assert any("generated_file_invalid" in item for item in verification["errors"])


def test_skill_verifier_always_validates_source_hash_record_shape(tmp_path: Path) -> None:
    root = _reviewed_vault(tmp_path)
    bundle = tmp_path / "bundle"
    with KnowledgeVault(root, read_only=True) as vault:
        build_skill_bundle(
            vault,
            bundle,
            skill_name="release-safety",
            description="Use reviewed release procedures.",
        )
    manifest_path = bundle / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["source_hashes"][0]["source_id"] = "source_invalid"
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")

    verification = verify_skill_bundle(bundle)

    assert verification["valid"] is False
    assert "source_hash_inventory_invalid" in verification["errors"]
