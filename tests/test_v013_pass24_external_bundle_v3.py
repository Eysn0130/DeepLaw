from __future__ import annotations

import base64
import hashlib
import json
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest

from benchmarks.release.external_qualification_bundle_v3 import (
    ExternalQualificationBundleV3Error,
    _check_json_projection,
    _record_sha256,
    validate_external_bundle,
)

SHA = "a" * 64
COMMIT = "1" * 40
TREE = "2" * 40


def _digest(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def _canonical(value: Any) -> bytes:
    return (
        json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
        + b"\n"
    )


def _write_json(path: Path, value: Any) -> bytes:
    raw = _canonical(value)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(raw)
    return raw


def _active(
    *,
    wheel_sha256: str,
    sdist_sha256: str,
    semantic_sha256: str,
    holdout_sha256: str,
    blind_sha256: str,
    isolation_sha256: str,
) -> dict[str, Any]:
    return {
        "schema_version": "deeplaw.v013-active-qualification/v1",
        "qualification_id": "deeplaw-v013-active-commercial-candidate",
        "status": "frozen_exact_candidate",
        "candidate_version": "0.13.0",
        "protocol_binding": {
            "protocol_id": "deeplaw-v013-source-candidate-qualification",
            "schema_version": "deeplaw.v013-qualification-protocol/v1",
            "relative_path": "benchmarks/v013/qualification-protocol-v1.json",
            "sha256": SHA,
        },
        "candidate_binding": {
            "source_commit": COMMIT,
            "source_tree": TREE,
            "lock_sha256": SHA,
            "wheel_filename": "deeplaw-0.13.0-py3-none-any.whl",
            "wheel_sha256": wheel_sha256,
            "sdist_filename": "deeplaw-0.13.0.tar.gz",
            "sdist_sha256": sdist_sha256,
            "artifact_manifest_sha256": SHA,
            "source_date_epoch": 1,
        },
        "external_inputs": {
            "human_gold_manifest_sha256": semantic_sha256,
            "qualification_holdout_sha256": holdout_sha256,
            "final_blind_holdout_sha256": blind_sha256,
            "compiler_scorer_isolation_sha256": isolation_sha256,
        },
        "host_constraints": {
            "codex": {
                "tool_version": "test",
                "model_id": "test-model",
                "reasoning_effort": "low",
            },
            "opencode": {
                "tool_version": "test",
                "model_id": "test-model",
                "reasoning_effort": None,
            },
        },
        "blocker": None,
        "release_ready": False,
        "claim_eligible": False,
    }


def _descriptor(path: Path, *, public_key: bytes = b"p" * 32, key_id: str | None = None) -> bytes:
    value = {
        "identity": "external-human-approver",
        "key_id": key_id or _digest(public_key),
        "public_key_b64": base64.b64encode(public_key).decode("ascii"),
    }
    return _write_json(path, value)


def _reference(root: Path, relative: str, *, kind: str, media_type: str) -> dict[str, Any]:
    raw = (root / relative).read_bytes()
    return {
        "relative_path": relative,
        "byte_size": len(raw),
        "sha256": _digest(raw),
        "media_type": media_type,
        "evidence_kind": kind,
    }


def _seed(tmp_path: Path) -> dict[str, Any]:
    root = tmp_path / "bundle"
    root.mkdir()
    descriptor_path = tmp_path / "trusted-approver.json"
    descriptor_raw = _descriptor(descriptor_path)

    wheel_raw = b"PK\x03\x04opaque-wheel"
    sdist_raw = b"\x1f\x8bopaque-sdist"
    (root / "retained").mkdir()
    (root / "retained/deeplaw-0.13.0-py3-none-any.whl").write_bytes(wheel_raw)
    (root / "retained/deeplaw-0.13.0.tar.gz").write_bytes(sdist_raw)

    semantic_raw = _write_json(root / "evidence/semantic-gold.json", {"gold": "external"})
    binding_raw = _write_json(root / "evidence/candidate-binding.json", {"binding": "candidate"})
    holdout_raw = _write_json(root / "evidence/holdout.json", {"holdout": "qualification"})
    blind_raw = _write_json(root / "evidence/blind.json", {"blind": "final"})
    runner_raw = _write_json(root / "evidence/runner.json", {"runner": "isolated"})
    scorer_raw = _write_json(root / "evidence/scorer.json", {"scorer": "isolated"})
    isolation_raw = _write_json(root / "evidence/isolation.json", {"isolation": "separate"})
    inventory_raw = _write_json(
        root / "candidate/candidate-full-raw-inventory.json",
        {"files": [{"name": "candidate-full.xml", "sha256": "b" * 64}]},
    )
    (root / "legal").mkdir()
    _write_json(root / "typed/manifest.json", {"schema_version": "typed-fixture/v1"})
    (root / "typed/receipt.xml").write_text("<receipt><item>ok</item></receipt>", encoding="utf-8")
    (root / "legal/original.pdf").write_bytes(b"%PDF-opaque-legal")
    (root / "legal/original.docx").write_bytes(b"PK\x03\x04opaque-docx")
    (root / "legal/original.html").write_text("<p>legal source</p>", encoding="utf-8")
    (root / "legal/original.md").write_text("# legal source\n", encoding="utf-8")

    files = [
        _reference(
            root,
            "retained/deeplaw-0.13.0-py3-none-any.whl",
            kind="retained_wheel",
            media_type="application/zip",
        ),
        _reference(
            root,
            "retained/deeplaw-0.13.0.tar.gz",
            kind="retained_sdist",
            media_type="application/gzip",
        ),
        _reference(
            root,
            "evidence/semantic-gold.json",
            kind="human_gold_scorer",
            media_type="application/json",
        ),
        _reference(
            root,
            "evidence/candidate-binding.json",
            kind="post_build_gold_binding",
            media_type="application/json",
        ),
        _reference(
            root,
            "evidence/holdout.json",
            kind="sanitized_supporting_receipt",
            media_type="application/json",
        ),
        _reference(
            root,
            "evidence/blind.json",
            kind="sanitized_supporting_receipt",
            media_type="application/json",
        ),
        _reference(
            root,
            "evidence/runner.json",
            kind="sanitized_supporting_receipt",
            media_type="application/json",
        ),
        _reference(
            root,
            "evidence/scorer.json",
            kind="sanitized_supporting_receipt",
            media_type="application/json",
        ),
        _reference(
            root,
            "evidence/isolation.json",
            kind="sanitized_supporting_receipt",
            media_type="application/json",
        ),
        _reference(
            root,
            "candidate/candidate-full-raw-inventory.json",
            kind="candidate_full_raw_inventory",
            media_type="application/json",
        ),
        _reference(
            root, "typed/manifest.json", kind="typed_manifest", media_type="application/json"
        ),
        _reference(root, "typed/receipt.xml", kind="typed_xml", media_type="application/xml"),
        _reference(
            root, "legal/original.pdf", kind="original_legal_pdf", media_type="application/pdf"
        ),
        _reference(
            root,
            "legal/original.docx",
            kind="original_legal_docx",
            media_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        ),
        _reference(root, "legal/original.html", kind="original_legal_html", media_type="text/html"),
        _reference(
            root, "legal/original.md", kind="original_legal_markdown", media_type="text/markdown"
        ),
    ]
    manifest: dict[str, Any] = {
        "schema_version": "deeplaw.external-qualification-bundle-manifest/v3",
        "candidate_run_id": 101,
        "evidence_run_id": 202,
        "candidate_binding": {
            "commit": COMMIT,
            "tree": TREE,
            "lock_sha256": SHA,
            "wheel_sha256": _digest(wheel_raw),
            "sdist_sha256": _digest(sdist_raw),
        },
        "external_inputs": {
            "semantic_gold_sha256": _digest(semantic_raw),
            "candidate_gold_binding_sha256": _digest(binding_raw),
            "qualification_holdout_sha256": _digest(holdout_raw),
            "final_blind_holdout_sha256": _digest(blind_raw),
            "runner_sha256": _digest(runner_raw),
            "scorer_sha256": _digest(scorer_raw),
            "compiler_scorer_isolation_sha256": _digest(isolation_raw),
        },
        "trusted_human_approver_descriptor_sha256": _digest(descriptor_raw),
        "candidate_full_raw_inventory_sha256": _digest(inventory_raw),
        "files": files,
    }
    manifest["record_sha256"] = _record_sha256(manifest)
    _write_json(root / "bundle-manifest.json", manifest)
    active = _active(
        wheel_sha256=_digest(wheel_raw),
        sdist_sha256=_digest(sdist_raw),
        semantic_sha256=_digest(semantic_raw),
        holdout_sha256=_digest(holdout_raw),
        blind_sha256=_digest(blind_raw),
        isolation_sha256=_digest(isolation_raw),
    )
    active_path = tmp_path / "active.json"
    _write_json(active_path, active)
    return {
        "root": root,
        "descriptor": descriptor_path,
        "active": active_path,
        "manifest": root / "bundle-manifest.json",
        "manifest_value": manifest,
    }


def _validate(
    paths: dict[str, Any], *, candidate_run_id: int = 101, evidence_run_id: int = 202
) -> dict[str, Any]:
    return validate_external_bundle(
        paths["root"],
        active_qualification=paths["active"],
        trusted_human_approver=paths["descriptor"],
        expected_candidate_run_id=candidate_run_id,
        expected_evidence_run_id=evidence_run_id,
    )


def test_v3_accepts_current_typed_and_opaque_artifacts_with_path_free_output(
    tmp_path: Path,
) -> None:
    paths = _seed(tmp_path)
    result = _validate(paths)
    assert result["schema_version"].endswith("/v3")
    assert result["candidate_run_id"] == 101
    assert result["evidence_run_id"] == 202
    assert result["file_count"] == result["referenced_file_count"] + 1
    assert "release_ready" not in result
    assert "claim_eligible" not in result
    assert result["bundle_manifest_sha256"] == _digest(paths["manifest"].read_bytes())


def test_v3_contract_admits_candidate_full_retained_supply_chain_manifest() -> None:
    schema = json.loads(
        (
            Path(__file__).resolve().parents[1]
            / "contracts/external-qualification-bundle-manifest.v3.schema.json"
        ).read_text(encoding="utf-8")
    )
    kinds = schema["$defs"]["file"]["properties"]["evidence_kind"]["enum"]
    assert "retained_supply_chain" in kinds


def test_v3_cli_requires_explicit_bindings_and_emits_only_derived_values(tmp_path: Path) -> None:
    paths = _seed(tmp_path)
    completed = subprocess.run(
        [
            sys.executable,
            "-m",
            "benchmarks.release.external_qualification_bundle_v3",
            "--root",
            str(paths["root"]),
            "--active-qualification",
            str(paths["active"]),
            "--candidate-run-id",
            "101",
            "--evidence-run-id",
            "202",
            "--trusted-human-approver",
            str(paths["descriptor"]),
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    assert completed.returncode == 0, completed.stderr
    result = json.loads(completed.stdout)
    assert "release_ready" not in result
    assert "claim_eligible" not in result
    assert str(paths["root"]) not in completed.stdout


def test_v3_rejects_orphan_file(tmp_path: Path) -> None:
    paths = _seed(tmp_path)
    (paths["root"] / "orphan.txt").write_text("orphan", encoding="utf-8")
    with pytest.raises(ExternalQualificationBundleV3Error, match=r"orphan|unreferenced"):
        _validate(paths)


def test_v3_rejects_symlink(tmp_path: Path) -> None:
    paths = _seed(tmp_path)
    target = paths["root"] / "legal/original.md"
    link = paths["root"] / "legal/link.md"
    try:
        link.symlink_to(target)
    except OSError:
        pytest.skip("symlink creation is unavailable")
    with pytest.raises(ExternalQualificationBundleV3Error, match="symbolic link"):
        _validate(paths)


@pytest.mark.parametrize("payload", ['{"x":1,"x":2}', '{"x":NaN}', '{"x":Infinity}'])
def test_v3_rejects_duplicate_and_non_finite_json(tmp_path: Path, payload: str) -> None:
    paths = _seed(tmp_path)
    paths["manifest"].write_text(payload, encoding="utf-8")
    with pytest.raises(
        ExternalQualificationBundleV3Error,
        match=r"strict JSON|non-finite|duplicate",
    ):
        _validate(paths)


@pytest.mark.parametrize("unsafe", ["/Users/private/evidence", "api_key=not-a-secret"])
def test_v3_rejects_secret_or_absolute_path_text(tmp_path: Path, unsafe: str) -> None:
    paths = _seed(tmp_path)
    unsafe_path = paths["root"] / "evidence/unsafe.txt"
    unsafe_path.write_text(unsafe, encoding="utf-8")
    manifest = json.loads(paths["manifest"].read_text(encoding="utf-8"))
    manifest["files"].append(
        _reference(
            paths["root"], "evidence/unsafe.txt", kind="sanitized_text", media_type="text/plain"
        )
    )
    manifest["record_sha256"] = _record_sha256(manifest)
    _write_json(paths["manifest"], manifest)
    with pytest.raises(ExternalQualificationBundleV3Error, match=r"Secret|absolute path"):
        _validate(paths)


def test_v3_rejects_binary_with_wrong_evidence_role(tmp_path: Path) -> None:
    paths = _seed(tmp_path)
    wrong = paths["root"] / "legal/wrong-role.pdf"
    wrong.write_bytes(b"%PDF-opaque")
    manifest = json.loads(paths["manifest"].read_text(encoding="utf-8"))
    manifest["files"].append(
        _reference(
            paths["root"],
            "legal/wrong-role.pdf",
            kind="sanitized_supporting_receipt",
            media_type="application/pdf",
        )
    )
    manifest["record_sha256"] = _record_sha256(manifest)
    _write_json(paths["manifest"], manifest)
    with pytest.raises(ExternalQualificationBundleV3Error, match="evidence kind and media type"):
        _validate(paths)


@pytest.mark.parametrize("mutation", ["candidate", "run", "trusted_key"])
def test_v3_rejects_self_hashed_but_wrong_cross_binding(tmp_path: Path, mutation: str) -> None:
    paths = _seed(tmp_path)
    if mutation == "candidate":
        manifest = json.loads(paths["manifest"].read_text(encoding="utf-8"))
        manifest["candidate_binding"]["commit"] = "3" * 40
        manifest["record_sha256"] = _record_sha256(manifest)
        _write_json(paths["manifest"], manifest)
    elif mutation == "run":
        pass
    else:
        public_key = b"q" * 32
        descriptor_raw = _descriptor(paths["descriptor"], public_key=public_key, key_id="f" * 64)
        manifest = json.loads(paths["manifest"].read_text(encoding="utf-8"))
        manifest["trusted_human_approver_descriptor_sha256"] = _digest(descriptor_raw)
        manifest["record_sha256"] = _record_sha256(manifest)
        _write_json(paths["manifest"], manifest)
    with pytest.raises(ExternalQualificationBundleV3Error):
        _validate(paths, candidate_run_id=999 if mutation == "run" else 101)


def test_v3_rejects_active_construction_candidate(tmp_path: Path) -> None:
    paths = _seed(tmp_path)
    active = json.loads(paths["active"].read_text(encoding="utf-8"))
    active["status"] = "construction_candidate"
    _write_json(paths["active"], active)
    with pytest.raises(
        ExternalQualificationBundleV3Error,
        match=r"schema validation|frozen exact",
    ):
        _validate(paths)


def test_v3_descriptor_is_closed_and_public_key_bound(tmp_path: Path) -> None:
    paths = _seed(tmp_path)
    _descriptor(paths["descriptor"], public_key=b"z" * 32, key_id="f" * 64)
    with pytest.raises(ExternalQualificationBundleV3Error, match=r"key id|descriptor"):
        _validate(paths)


def test_v3_allows_only_false_auth_receipts_and_non_sensitive_digests() -> None:
    _check_json_projection(
        {
            "auth_file_read": False,
            "auth_store_read": False,
            "authentication_material_read": False,
            "credential_value_recorded": False,
            "auth_file_sha256": SHA,
            "auth_store_sha256": SHA,
            "authentication_material_sha256": SHA,
            "credential_path_sha256": SHA,
            "credential_value_sha256": SHA,
        }
    )
    with pytest.raises(ExternalQualificationBundleV3Error, match="must be false"):
        _check_json_projection({"auth_file_read": True})
    with pytest.raises(ExternalQualificationBundleV3Error, match="digest is invalid"):
        _check_json_projection({"auth_store_sha256": "not-a-digest"})
    with pytest.raises(ExternalQualificationBundleV3Error, match="Secret-shaped"):
        _check_json_projection({"private_key": "not-retained"})


def test_v3_does_not_read_an_in_bundle_trusted_descriptor(tmp_path: Path) -> None:
    paths = _seed(tmp_path)
    inside = paths["root"] / "trusted.json"
    _descriptor(inside)
    with pytest.raises(ExternalQualificationBundleV3Error, match="outside"):
        validate_external_bundle(
            paths["root"],
            active_qualification=paths["active"],
            trusted_human_approver=inside,
            expected_candidate_run_id=101,
            expected_evidence_run_id=202,
        )
