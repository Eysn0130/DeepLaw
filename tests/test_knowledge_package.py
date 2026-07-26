from __future__ import annotations

import json
import zipfile
from pathlib import Path

import pytest
from jsonschema import Draft202012Validator

import deeplaw.knowledge_package as knowledge_package
from deeplaw.knowledge_compiler import compile_source
from deeplaw.knowledge_package import (
    export_knowledge_package,
    import_knowledge_package,
    verify_knowledge_package,
)
from deeplaw.knowledge_store import KnowledgeVault, initialize_knowledge_vault
from deeplaw.util import canonical_json, sha256_bytes, stable_id


def _vault_with_assets(tmp_path: Path) -> tuple[Path, str, str]:
    root = tmp_path / "source-vault"
    initialize_knowledge_vault(root, name="source", scope="team")
    with KnowledgeVault(root, read_only=False) as vault:
        public = vault.propose_asset(
            kind="rule",
            memory_tier="domain",
            title="Public rule",
            statement="Public shared knowledge must preserve provenance.",
            sensitivity="public",
            semantic_key="shared.provenance",
        )
        private = vault.propose_asset(
            kind="fact",
            memory_tier="project",
            title="Private fact",
            statement="Private project knowledge must not export by default.",
            sensitivity="private",
            semantic_key="private.project.fact",
        )
        public_id = vault.approve_asset(
            public.asset_id,
            confirm_reviewed=True,
        ).asset_id
        private_id = vault.approve_asset(
            private.asset_id,
            confirm_reviewed=True,
        ).asset_id
    return root, public_id, private_id


def test_portable_package_is_content_verifiable_and_excludes_private_by_default(
    tmp_path: Path,
) -> None:
    root, public_id, private_id = _vault_with_assets(tmp_path)
    package = tmp_path / "assets.dlk"
    with KnowledgeVault(root, read_only=True) as vault:
        exported = export_knowledge_package(vault, package)
    verification = verify_knowledge_package(package)

    assert exported["asset_count"] == 1
    assert exported["policy"]["max_sensitivity"] == "public"
    assert exported["policy"]["publisher_identity_verified"] is False
    assert verification["valid"] is True

    with zipfile.ZipFile(package) as archive:
        assets = archive.read("assets.jsonl").decode()
        manifest = json.loads(archive.read("manifest.json"))
    assert public_id in assets
    assert private_id not in assets
    repository = Path(__file__).resolve().parents[1]
    schema = json.loads(
        (repository / "contracts/knowledge-package.v1.schema.json").read_text()
    )
    Draft202012Validator(schema).validate(manifest)


def test_package_tampering_and_duplicate_paths_are_rejected(tmp_path: Path) -> None:
    root, _, _ = _vault_with_assets(tmp_path)
    package = tmp_path / "assets.dlk"
    with KnowledgeVault(root, read_only=True) as vault:
        export_knowledge_package(vault, package)
    with (
        pytest.warns(UserWarning, match="Duplicate name"),
        zipfile.ZipFile(package, "a") as archive,
    ):
        archive.writestr("assets.jsonl", b'{"tampered":true}\n')

    with pytest.raises(ValueError, match="duplicate or unsafe paths"):
        verify_knowledge_package(package)


def test_package_verification_rejects_a_symbolic_link(tmp_path: Path) -> None:
    root, _, _ = _vault_with_assets(tmp_path)
    package = tmp_path / "assets.dlk"
    linked_package = tmp_path / "linked.dlk"
    with KnowledgeVault(root, read_only=True) as vault:
        export_knowledge_package(vault, package)
    linked_package.symlink_to(package)

    with pytest.raises(ValueError, match="non-symlink"):
        verify_knowledge_package(linked_package)


def test_import_never_launders_remote_status_or_trust(tmp_path: Path) -> None:
    source_root, _, _ = _vault_with_assets(tmp_path)
    package = tmp_path / "assets.dlk"
    with KnowledgeVault(source_root, read_only=True) as source:
        export_knowledge_package(source, package)

    target_root = tmp_path / "target-vault"
    initialize_knowledge_vault(target_root, name="target", scope="personal")
    with KnowledgeVault(target_root, read_only=False) as target:
        with pytest.raises(ValueError, match="remain quarantined"):
            import_knowledge_package(
                target,
                package,
                confirm_untrusted=False,
            )
        result = import_knowledge_package(
            target,
            package,
            confirm_untrusted=True,
        )
        imported = [
            target.get_asset(asset_id, include_inactive=True)
            for asset_id in result["imported_asset_ids"]
        ]

    assert result["status"] == "quarantined"
    assert result["publisher_identity_verified"] is False
    assert imported
    assert {asset.status for asset in imported} == {"quarantined"}
    assert {asset.trust for asset in imported} == {"untrusted"}
    assert all(asset.verification == "unverified" for asset in imported)


def test_private_export_requires_an_explicit_sensitivity_policy(tmp_path: Path) -> None:
    root, public_id, private_id = _vault_with_assets(tmp_path)
    package = tmp_path / "private-assets.dlk"
    with KnowledgeVault(root, read_only=True) as vault:
        exported = export_knowledge_package(
            vault,
            package,
            max_sensitivity="private",
        )

    with zipfile.ZipFile(package) as archive:
        assets = archive.read("assets.jsonl").decode()
    assert exported["asset_count"] == 2
    assert public_id in assets
    assert private_id in assets


def test_package_export_rejects_relation_evidence_above_the_sensitivity_ceiling(
    tmp_path: Path,
) -> None:
    root = tmp_path / "relation-vault"
    initialize_knowledge_vault(root, name="relation", scope="project")
    source = tmp_path / "restricted.md"
    source.write_text(
        "# Restricted evidence\nSensitive provenance for a portable relation.",
        encoding="utf-8",
    )
    with KnowledgeVault(root, read_only=False) as vault:
        first = vault.propose_asset(
            kind="fact",
            memory_tier="project",
            title="Public subject",
            statement="Public subject knowledge.",
            sensitivity="public",
        )
        second = vault.propose_asset(
            kind="fact",
            memory_tier="project",
            title="Public object",
            statement="Public object knowledge.",
            sensitivity="public",
        )
        first = vault.approve_asset(first.asset_id, confirm_reviewed=True)
        second = vault.approve_asset(second.asset_id, confirm_reviewed=True)
        compiled = compile_source(
            vault,
            source,
            source_kind="document",
            sensitivity="restricted",
            confirm_no_case_data=True,
        )
        evidence_asset = vault.get_asset(
            compiled["asset_ids"][0],
            include_inactive=True,
        )
        vault.add_relation(
            subject_asset_id=first.asset_id,
            predicate="supports",
            object_asset_id=second.asset_id,
            evidence_fragment_id=evidence_asset.source_refs[0].fragment_id,
            confirm_reviewed=True,
        )
    with (
        KnowledgeVault(root, read_only=True) as vault,
        pytest.raises(ValueError, match="exceeds the package sensitivity"),
    ):
        export_knowledge_package(vault, tmp_path / "unsafe.dlk")


def test_package_export_is_reproducible_for_the_same_vault_revision(
    tmp_path: Path,
) -> None:
    root, _, _ = _vault_with_assets(tmp_path)
    first = tmp_path / "first.dlk"
    second = tmp_path / "second.dlk"
    with KnowledgeVault(root, read_only=True) as vault:
        export_knowledge_package(vault, first)
        export_knowledge_package(vault, second)

    assert first.read_bytes() == second.read_bytes()


def test_package_export_does_not_overwrite_an_existing_file(tmp_path: Path) -> None:
    root, _, _ = _vault_with_assets(tmp_path)
    package = tmp_path / "existing.dlk"
    package.write_bytes(b"keep-existing-package")

    with (
        KnowledgeVault(root, read_only=True) as vault,
        pytest.raises(FileExistsError, match="already exists"),
    ):
        export_knowledge_package(vault, package)

    assert package.read_bytes() == b"keep-existing-package"


def test_manifest_metadata_is_covered_by_the_package_identity(tmp_path: Path) -> None:
    root, _, _ = _vault_with_assets(tmp_path)
    package = tmp_path / "assets.dlk"
    tampered = tmp_path / "tampered.dlk"
    with KnowledgeVault(root, read_only=True) as vault:
        export_knowledge_package(vault, package)

    with zipfile.ZipFile(package) as archive:
        entries = {info.filename: archive.read(info) for info in archive.infolist()}
    manifest = json.loads(entries["manifest.json"])
    manifest["source_vault"]["name"] = "forged publisher"
    entries["manifest.json"] = (
        json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    ).encode()
    with zipfile.ZipFile(tampered, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for name, payload in entries.items():
            archive.writestr(name, payload)

    verification = verify_knowledge_package(tampered)
    assert verification["package_id_valid"] is False
    assert verification["valid"] is False


def test_self_consistent_package_hashes_cannot_hide_an_invalid_asset_record(
    tmp_path: Path,
) -> None:
    root, _, _ = _vault_with_assets(tmp_path)
    package = tmp_path / "assets.dlk"
    forged = tmp_path / "forged.dlk"
    with KnowledgeVault(root, read_only=True) as vault:
        export_knowledge_package(vault, package)

    with zipfile.ZipFile(package) as archive:
        entries = {info.filename: archive.read(info) for info in archive.infolist()}
    assets = [
        json.loads(line)
        for line in entries["assets.jsonl"].decode().splitlines()
    ]
    assets[0]["statement"] = "A forged statement with a stale content hash."
    entries["assets.jsonl"] = (
        "\n".join(canonical_json(asset) for asset in assets) + "\n"
    ).encode()
    manifest = json.loads(entries["manifest.json"])
    asset_file = next(
        item for item in manifest["files"] if item["path"] == "assets.jsonl"
    )
    asset_file["byte_size"] = len(entries["assets.jsonl"])
    asset_file["sha256"] = sha256_bytes(entries["assets.jsonl"])
    manifest["package_id"] = stable_id(
        "knowledgepkg",
        sha256_bytes(
            canonical_json(knowledge_package._package_basis(manifest)).encode()
        ),
        length=32,
    )
    entries["manifest.json"] = (
        json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    ).encode()
    with zipfile.ZipFile(forged, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for name, payload in entries.items():
            archive.writestr(name, payload)

    verification = verify_knowledge_package(forged)
    assert verification["package_id_valid"] is True
    assert all(check["valid"] for check in verification["file_checks"])
    assert verification["asset_records_valid"] is False
    assert verification["valid"] is False


def test_source_bound_package_preserves_compiler_and_optional_evidence(
    tmp_path: Path,
) -> None:
    root = tmp_path / "compiled-vault"
    initialize_knowledge_vault(root, name="compiled", scope="project")
    source = tmp_path / "source.md"
    source.write_text(
        "# Stable source\n"
        "A source-bound public knowledge asset with reproducible compiler provenance.\n",
        encoding="utf-8",
    )
    with KnowledgeVault(root, read_only=False) as vault:
        ingested = compile_source(
            vault,
            source,
            source_kind="document",
            sensitivity="public",
            confirm_no_case_data=True,
        )
        vault.approve_asset(ingested["asset_ids"][0], confirm_reviewed=True)
    package = tmp_path / "source-bound.dlk"
    with KnowledgeVault(root, read_only=True) as vault:
        export_knowledge_package(
            vault,
            package,
            include_evidence_text=True,
            include_source_files=True,
        )

    verification = verify_knowledge_package(package)
    assert verification["valid"] is True
    assert verification["asset_records_valid"] is True
    assert verification["source_records_valid"] is True
    assert verification["fragment_records_valid"] is True
    assert verification["asset_source_links_valid"] is True
    with zipfile.ZipFile(package) as archive:
        sources = [
            json.loads(line)
            for line in archive.read("sources.jsonl").decode().splitlines()
        ]
        names = archive.namelist()
    assert sources[0]["compiler"]["compiled_fragment_sha256"]
    assert any(name.startswith("source-files/") for name in names)
