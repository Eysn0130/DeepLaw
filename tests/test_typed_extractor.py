from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

from deeplaw.knowledge_compiler import compile_source
from deeplaw.knowledge_store import KnowledgeVault, initialize_knowledge_vault
from deeplaw.util import sha256_file


def _vault(tmp_path: Path) -> Path:
    root = tmp_path / "vault"
    initialize_knowledge_vault(root, name="typed", scope="project")
    return root


def _sidecar(tmp_path: Path, *, invalid: bool = False) -> Path:
    path = tmp_path / "typed_sidecar.py"
    if invalid:
        source = "import sys\nsys.stdin.buffer.read()\nsys.stdout.write('not-json')\n"
    else:
        source = """\
import json
import sys

request = json.load(sys.stdin)
assert request["schema_version"] == "deeplaw.typed-extractor-request/v1"
output = {
    "schema_version": "deeplaw.typed-extractor-output/v1",
    "proposals": [
        {
            "kind": "requirement",
            "title": "Bound both sections",
            "statement": "Alpha and beta must be evaluated together.",
            "source_ref_indexes": [0, 1],
            "semantic_key_hint": "alpha-beta",
            "applicability": {"environment": "test"},
            "observed_at": None,
            "valid_from": None,
            "valid_to": None,
            "expires_at": None,
            "project_scope": None,
            "repository_scope": None,
            "branch_scope": None,
            "version_scope": None,
            "environment_scope": "test",
            "warnings": [],
        },
        {
            "kind": "risk",
            "title": "Alpha risk",
            "statement": "Alpha can fail when the bound is omitted.",
            "source_ref_indexes": [0],
            "semantic_key_hint": None,
            "applicability": {},
            "observed_at": "2026-07-27T00:00:00Z",
            "valid_from": None,
            "valid_to": None,
            "expires_at": None,
            "project_scope": None,
            "repository_scope": None,
            "branch_scope": None,
            "version_scope": None,
            "environment_scope": None,
            "warnings": ["review the model interpretation"],
        },
    ],
}
json.dump(output, sys.stdout, ensure_ascii=False, separators=(",", ":"))
"""
    path.write_text(source, encoding="utf-8")
    return path.absolute()


def _manifest(tmp_path: Path, sidecar: Path, *, mode: str) -> Path:
    model = tmp_path / "model.bin"
    model.write_bytes(b"fixed-test-model")
    manifest = {
        "schema_version": "deeplaw.typed-extractor-manifest/v1",
        "mode": mode,
        "extractor": "test-closed-sidecar",
        "extractor_revision": "test-closed-sidecar/1",
        "command": [str(Path(sys.executable).resolve()), str(sidecar)],
        "model_identity": "test-model",
        "model_revision": "sha-test-1",
        "model_files": [
            {"path": str(sidecar), "sha256": sha256_file(sidecar)},
            {"path": str(model), "sha256": sha256_file(model)},
        ],
        "prompt_config_sha256": "a" * 64,
        "network_policy": (
            "offline" if mode == "local-model-v1" else "explicit-external"
        ),
        "environment": [],
        "max_input_chars": 200_000,
        "max_output_bytes": 200_000,
        "timeout_seconds": 30,
    }
    path = tmp_path / f"{mode}.json"
    path.write_text(json.dumps(manifest), encoding="utf-8")
    return path


def test_local_typed_sidecar_is_bounded_source_bound_and_untrusted(
    tmp_path: Path,
) -> None:
    root = _vault(tmp_path)
    source = tmp_path / "source.md"
    source.write_text("# Alpha\nAlpha evidence.\n\n# Beta\nBeta evidence.\n", encoding="utf-8")
    sidecar = _sidecar(tmp_path)
    manifest = _manifest(tmp_path, sidecar, mode="local-model-v1")

    with KnowledgeVault(root, read_only=False) as vault:
        result = compile_source(
            vault,
            source,
            source_kind="document",
            confirm_no_case_data=True,
            typed_extraction="local-model-v1",
            typed_extractor_manifest=manifest,
        )
        assets = [
            vault.get_asset(asset_id, include_inactive=True)
            for asset_id in result["asset_ids"]
        ]
        review = vault.source_review_manifest(result["source"]["source_id"])
        proposal_set = vault.connection.execute(
            "SELECT extractor, extractor_revision, model_identity FROM proposal_sets_v2 "
            "WHERE proposal_set_id = ?",
            (result["identity"]["proposal_set_id"],),
        ).fetchone()

    assert review["fragment_count"] == 2
    assert review["proposal_count"] == 4
    assert len(assets[2].source_refs) == 2
    assert assets[2].trust == "untrusted"
    assert assets[2].status == "proposed"
    assert proposal_set["extractor"] == "test-closed-sidecar"
    assert proposal_set["extractor_revision"] == "test-closed-sidecar/1"
    assert proposal_set["model_identity"] == "test-model@sha-test-1"
    serialized = json.dumps(result, ensure_ascii=False)
    assert str(tmp_path) not in serialized


def test_external_typed_sidecar_requires_explicit_disclosure(tmp_path: Path) -> None:
    root = _vault(tmp_path)
    source = tmp_path / "source.md"
    source.write_text("# Alpha\nAlpha evidence.\n\n# Beta\nBeta evidence.\n", encoding="utf-8")
    sidecar = _sidecar(tmp_path)
    manifest = _manifest(tmp_path, sidecar, mode="external-model-explicit")

    with KnowledgeVault(root, read_only=False) as vault:
        with pytest.raises(ValueError, match="explicit confirmation"):
            compile_source(
                vault,
                source,
                source_kind="document",
                confirm_no_case_data=True,
                typed_extraction="external-model-explicit",
                typed_extractor_manifest=manifest,
            )
        result = compile_source(
            vault,
            source,
            source_kind="document",
            confirm_no_case_data=True,
            typed_extraction="external-model-explicit",
            typed_extractor_manifest=manifest,
            confirm_external_disclosure=True,
        )

    assert result["compiler"]["typed_extractor"]["disclosure"].startswith(
        "section text"
    )


def test_invalid_typed_sidecar_fails_before_canonical_write(tmp_path: Path) -> None:
    root = _vault(tmp_path)
    source = tmp_path / "source.md"
    source.write_text("# Alpha\nAlpha evidence.\n", encoding="utf-8")
    sidecar = _sidecar(tmp_path, invalid=True)
    manifest = _manifest(tmp_path, sidecar, mode="local-model-v1")

    with KnowledgeVault(root, read_only=False) as vault:
        with pytest.raises(RuntimeError, match="invalid JSON"):
            compile_source(
                vault,
                source,
                source_kind="document",
                confirm_no_case_data=True,
                typed_extraction="local-model-v1",
                typed_extractor_manifest=manifest,
            )
        assert vault.all_sources() == ()
