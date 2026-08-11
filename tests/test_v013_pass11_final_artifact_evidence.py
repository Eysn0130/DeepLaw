from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path

from jsonschema import Draft202012Validator

from benchmarks.release.evidence import verify_record_digest

REPOSITORY = Path(__file__).resolve().parents[1]
EVIDENCE = (
    REPOSITORY / "benchmarks/release/evidence/pass11-final-artifact-2026-08-11"
)
MANIFEST = EVIDENCE / "pass11-final-artifact-evidence.json"
LOCAL_PATH = re.compile(
    r"(?:/Users/|/home/|/tmp/|/private/var/|file:///|"
    r"(?<![A-Za-z0-9])[A-Za-z]:[\\/](?:[^\\/\s<>\"']+[\\/])+"
    r"[^\\/\s<>\"']+)"
)


def _load(name: str) -> dict[str, object]:
    value = json.loads((EVIDENCE / name).read_text(encoding="utf-8"))
    assert isinstance(value, dict)
    return value


def test_pass11_final_artifact_manifest_binds_every_retained_report() -> None:
    manifest = _load(MANIFEST.name)
    assert manifest["candidate"] == {
        "git_commit": "9aea8598b231ae85a70a478f37682a9b7a17f024",
        "git_tree": "425d07324c22de051e2bf00fe04e7a9b80de9e2b",
        "package_version": "0.12.0",
        "working_tree_clean": True,
    }
    reports = manifest["retained_reports"]
    assert isinstance(reports, list)
    expected_names = {
        path.name
        for path in EVIDENCE.iterdir()
        if path.is_file() and path.name != MANIFEST.name
    }
    assert {item["name"] for item in reports} == expected_names
    for item in reports:
        payload = (EVIDENCE / item["name"]).read_bytes()
        assert item["bytes"] == len(payload)
        assert item["sha256"] == hashlib.sha256(payload).hexdigest()
        assert LOCAL_PATH.search(payload.decode("utf-8")) is None


def test_pass11_reproducible_build_and_lifecycle_bind_same_distributions() -> None:
    manifest = _load(MANIFEST.name)
    reproducible = _load("reproducible-build.json")
    lifecycle = _load("distribution-lifecycle.json")
    schema = json.loads(
        (
            REPOSITORY / "contracts/reproducible-build-report.v2.schema.json"
        ).read_text(encoding="utf-8")
    )
    Draft202012Validator(schema).validate(reproducible)
    verify_record_digest(reproducible, field="Pass 11 reproducible build")
    verify_record_digest(lifecycle, field="Pass 11 distribution lifecycle")

    assert reproducible["reproducible"] is True
    assert reproducible["working_tree_dirty"] is False
    assert reproducible["binding"]["commit"] == manifest["candidate"]["git_commit"]
    assert reproducible["binding"]["tree"] == manifest["candidate"]["git_tree"]
    assert lifecycle["passed"] is True
    assert lifecycle["gates"] == {
        "cli_version": True,
        "locked_runtime_constraints": True,
        "sdist_install": True,
        "sdist_uninstall": True,
        "upgrade_from_0_6_0": True,
        "wheel_install": True,
        "wheel_uninstall": True,
    }
    reproducible_artifacts = {
        item["name"]: {"bytes": item["byte_size"], "sha256": item["sha256"]}
        for item in reproducible["artifacts"]
    }
    for kind in ("wheel", "sdist"):
        expected = manifest["distributions"][kind]
        assert reproducible_artifacts[expected["name"]] == {
            "bytes": expected["bytes"],
            "sha256": expected["sha256"],
        }
        assert lifecycle["artifacts"][kind]["sha256"] == expected["sha256"]


def test_pass11_sbom_license_and_audits_are_local_evidence_only() -> None:
    manifest = _load(MANIFEST.name)
    sbom = _load("deeplaw-0.12.0.cdx.json")
    licenses = _load("deeplaw-0.12.0-licenses.json")
    assert sbom["bomFormat"] == "CycloneDX"
    assert sbom["specVersion"] == "1.5"
    assert sbom["metadata"]["component"]["version"] == "0.12.0"
    assert licenses["status"] == "passed"
    assert licenses["blocked"] == []
    assert licenses["review_required"] == []
    for profile in ("default", "build", "discovery", "document-engine"):
        audit = _load(f"audit-{profile}.json")
        verify_record_digest(audit, field=f"Pass 11 {profile} audit")
        assert audit["status"] == "passed"
        assert audit["profile"] == profile

    assert len(manifest["darwin_install_smoke"]) == 3
    assert {
        tuple(item["python"].split(".")[:2])
        for item in manifest["darwin_install_smoke"]
    } == {
        ("3", "11"),
        ("3", "12"),
        ("3", "13"),
    }
    assert manifest["claim_eligible"] is False
    assert manifest["release_gate_passed"] is False
    assert manifest["release_ready"] is False
    assert all(manifest["not_executed"].values())
    assert manifest["version_change_authorized"] is False
    assert manifest["tag_authorized"] is False
    assert manifest["publication_authorized"] is False
