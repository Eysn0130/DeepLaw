from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

import pytest

from benchmarks.release import assemble_commercial_qualification_v9 as assembler
from benchmarks.release import kernel_qualification_bundle_v1 as bundle
from benchmarks.release import release_provenance_v9 as provenance
from tests.test_v013_commercial_qualification_v9 import _install_parser
from tests.test_v013_kernel_qualification_bundle_v1 import (
    EXPECTED_CANDIDATE,
    RUN_IDS,
    _make_fixture,
    _write_json,
)


def _sha(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def _environment() -> dict[str, Any]:
    return {
        "platform_system": "Darwin",
        "platform_release": "fixture",
        "platform_version": "fixture",
        "machine": "arm64",
        "python_implementation": "CPython",
        "python_version": "3.13.0",
        "python_executable_name": "python",
        "uv_version": "uv 0.9.0",
        "ci": True,
        "github_actions": True,
        "github_runner_os": "macOS",
        "github_runner_arch": "ARM64",
    }


def _fixture(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> tuple[Path, Path, Path, dict[str, Any]]:
    assets = tmp_path / "assets"
    bundle_root = assets / "evidence" / "kernel-bundle"
    bundle_root.mkdir(parents=True)

    dist = assets / "dist"
    dist.mkdir()
    wheel_raw = b"exact v0.13 wheel fixture\n"
    sdist_raw = b"exact v0.13 sdist fixture\n"
    wheel_path = dist / "deeplaw-0.13.0-py3-none-any.whl"
    sdist_path = dist / "deeplaw-0.13.0.tar.gz"
    wheel_path.write_bytes(wheel_raw)
    sdist_path.write_bytes(sdist_raw)
    monkeypatch.setitem(EXPECTED_CANDIDATE, "wheel_sha256", _sha(wheel_raw))
    monkeypatch.setitem(EXPECTED_CANDIDATE, "sdist_sha256", _sha(sdist_raw))

    retained_manifest = {
        "schema_version": "deeplaw.retained-candidate-artifacts/v1",
        "package_version": "0.13.0",
        "release_ready": False,
        "claim_eligible": False,
        "git_commit": EXPECTED_CANDIDATE["commit"],
        "git_tree": EXPECTED_CANDIDATE["tree"],
        "lock_sha256": EXPECTED_CANDIDATE["lock_sha256"],
        "wheel": {
            "filename": wheel_path.name,
            "sha256": EXPECTED_CANDIDATE["wheel_sha256"],
            "bytes": len(wheel_raw),
        },
        "sdist": {
            "filename": sdist_path.name,
            "sha256": EXPECTED_CANDIDATE["sdist_sha256"],
            "bytes": len(sdist_raw),
        },
    }
    retained_path = assets / "retained-candidate-artifacts.json"
    _write_json(retained_path, retained_manifest)
    retained_raw = retained_path.read_bytes()

    auxiliary: dict[str, tuple[str, bytes]] = {}
    for role in ("sbom", "openvex", "licenses", "provenance"):
        raw = f'{{"fixture":"{role}"}}\n'.encode()
        relative = f"supply/{role}.json"
        path = assets / relative
        path.parent.mkdir(exist_ok=True)
        path.write_bytes(raw)
        auxiliary[role] = (relative, raw)
    pre_publish: dict[str, Any] = {
        "schema_version": "deeplaw.pre-publish-artifact-gate/v1",
        "status": "pre_publish_passed",
        "created_at": "2026-08-21T00:00:00Z",
        "candidate": {
            "commit": EXPECTED_CANDIDATE["commit"],
            "tree": EXPECTED_CANDIDATE["tree"],
            "lock_sha256": EXPECTED_CANDIDATE["lock_sha256"],
        },
        "builds": {
            "count": 2,
            "byte_identical": True,
            "first": {
                "build_id": "first",
                "wheel_sha256": EXPECTED_CANDIDATE["wheel_sha256"],
                "sdist_sha256": EXPECTED_CANDIDATE["sdist_sha256"],
                "receipt_sha256": "a" * 64,
            },
            "second": {
                "build_id": "second",
                "wheel_sha256": EXPECTED_CANDIDATE["wheel_sha256"],
                "sdist_sha256": EXPECTED_CANDIDATE["sdist_sha256"],
                "receipt_sha256": "b" * 64,
            },
        },
        "retained_artifacts": {
            "manifest_sha256": _sha(retained_raw),
            "manifest_path": retained_path.relative_to(assets).as_posix(),
            "wheel": {
                "name": wheel_path.name,
                "sha256": EXPECTED_CANDIDATE["wheel_sha256"],
                "byte_size": len(wheel_raw),
                "retained_path": wheel_path.relative_to(assets).as_posix(),
            },
            "sdist": {
                "name": sdist_path.name,
                "sha256": EXPECTED_CANDIDATE["sdist_sha256"],
                "byte_size": len(sdist_raw),
                "retained_path": sdist_path.relative_to(assets).as_posix(),
            },
        },
        **{
            role: {
                "format": role,
                "sha256": _sha(raw),
                "path": relative,
                "verified": True,
            }
            for role, (relative, raw) in auxiliary.items()
        },
        "record_sha256": "",
    }
    pre_publish["record_sha256"] = provenance.record_sha256(pre_publish)

    external_identity = _make_fixture(bundle_root, monkeypatch)
    pre_path = bundle_root / "typed" / "pre-publish-source.json"
    _write_json(pre_path, pre_publish)
    pre_raw = pre_path.read_bytes()
    supply_path = bundle_root / "typed" / "retained_supply_chain.json"
    supply = json.loads(supply_path.read_text(encoding="utf-8"))
    supply["payload"]["pre_publish_receipt_source"] = {
        "relative_path": pre_path.name,
        "byte_size": len(pre_raw),
        "sha256": _sha(pre_raw),
        "media_type": "application/json",
    }
    supply["record_sha256"] = bundle.record_sha256(supply)
    _write_json(supply_path, supply)
    for path in sorted((bundle_root / "typed").glob("host_event_sequence-*.json")):
        value = json.loads(path.read_text(encoding="utf-8"))
        host = "codex" if "codex" in path.name else "opencode"
        index = int(path.stem.rsplit("-", 1)[-1])
        value["run_binding"]["run_id"] = f"fixture-{host}-{index}"
        value["record_sha256"] = bundle.record_sha256(value)
        _write_json(path, value)
    bundle.build_bundle(
        bundle_root,
        run_ids=RUN_IDS,
        expected_candidate=EXPECTED_CANDIDATE,
        host_identity_input=external_identity,
    )
    _install_parser(monkeypatch)
    assembled = assembler.assemble_commercial_qualification(
        bundle_root=bundle_root,
        output_root=assets,
    )
    return assets, bundle_root, pre_path, assembled


def test_v9_release_provenance_reassembles_exact_core_and_keeps_optional_claims_false(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    assets, bundle_root, pre_path, assembled = _fixture(tmp_path, monkeypatch)
    manifest = provenance.build_release_manifest(
        assets_root=assets,
        bundle_root=bundle_root,
        report_path=assets / assembled["report_path"],
        pre_publish_receipt_path=pre_path,
        release_commit=EXPECTED_CANDIDATE["commit"],
        release_tree=EXPECTED_CANDIDATE["tree"],
        candidate_run_id=RUN_IDS["candidate_run_id"],
        evidence_run_id=RUN_IDS["evidence_run_id"],
        qualification_run_id=RUN_IDS["qualification_run_id"],
        environment=_environment(),
    )
    assert manifest["release_ready"] is True
    assert manifest["kernel_release_claim_eligible"] is True
    assert manifest["human_attested_claim_eligible"] is False
    assert manifest["competitive_claim_eligible"] is False
    assert all(
        row["status"] == "not_executed" and row["claim_eligible"] is False
        for row in (
            *manifest["gate_evidence"]["capability_claims"],
            *manifest["gate_evidence"]["competitive_research_claims"],
        )
    )

    manifest_path = assets / "commercial-release-manifest.json"
    manifest_path.write_bytes(provenance.canonical_json(manifest) + b"\n")
    checked = provenance.validate_release_provenance(
        manifest_path,
        assets_root=assets,
        bundle_root=bundle_root,
        report_path=assets / assembled["report_path"],
        pre_publish_receipt_path=pre_path,
    )
    assert checked["status"] == "transitive_provenance_validated"
    assert checked["release_ready"] is True


def test_v9_release_provenance_rejects_release_tree_or_retained_gate_tamper(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    assets, bundle_root, pre_path, assembled = _fixture(tmp_path, monkeypatch)
    with pytest.raises(provenance.ReleaseProvenanceV9Error, match="release commit"):
        provenance.build_release_manifest(
            assets_root=assets,
            bundle_root=bundle_root,
            report_path=assets / assembled["report_path"],
            pre_publish_receipt_path=pre_path,
            release_commit="9" * 40,
            release_tree=EXPECTED_CANDIDATE["tree"],
            **RUN_IDS,
            environment=_environment(),
        )
    with pytest.raises(provenance.ReleaseProvenanceV9Error, match="release tree"):
        provenance.build_release_manifest(
            assets_root=assets,
            bundle_root=bundle_root,
            report_path=assets / assembled["report_path"],
            pre_publish_receipt_path=pre_path,
            release_commit=EXPECTED_CANDIDATE["commit"],
            release_tree="8" * 40,
            **RUN_IDS,
            environment=_environment(),
        )

    gate = assets / "evidence" / "gate-results" / "scale_performance.json"
    gate.write_bytes(gate.read_bytes() + b" ")
    with pytest.raises(provenance.ReleaseProvenanceV9Error, match="independent assembly"):
        provenance.build_release_manifest(
            assets_root=assets,
            bundle_root=bundle_root,
            report_path=assets / assembled["report_path"],
            pre_publish_receipt_path=pre_path,
            release_commit=EXPECTED_CANDIDATE["commit"],
            release_tree=EXPECTED_CANDIDATE["tree"],
            **RUN_IDS,
            environment=_environment(),
        )
