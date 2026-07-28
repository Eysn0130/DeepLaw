from __future__ import annotations

import json
from pathlib import Path

import pytest
from jsonschema import Draft202012Validator

from benchmarks.release.commercial_release import (
    COMPETITIVE_EVIDENCE_MISSING,
    _docs,
    _unified_versions,
)
from benchmarks.release.evidence import verify_record_digest, write_report
from benchmarks.release.platform_gate import (
    REQUIRED_TEST_MODULES,
    WINDOWS_NATIVE_TESTS,
    PlatformGateError,
    _junit_report,
)

REPOSITORY = Path(__file__).resolve().parents[1]


def _junit(path: Path, *, skipped: int = 0) -> None:
    modules = sorted(REQUIRED_TEST_MODULES)
    cases = [
        f'<testcase classname="{module}" name="test_required_{index}" />'
        for index, module in enumerate(modules)
    ]
    cases.extend(
        f'<testcase classname="tests.test_windows_acl" name="{name}" />'
        for name in sorted(WINDOWS_NATIVE_TESTS)
    )
    cases.extend(
        f'<testcase classname="tests.test_complete" name="test_{index}" />'
        for index in range(580 - len(cases))
    )
    if skipped:
        cases[0] = cases[0].replace(" />", "><skipped /></testcase>")
    path.write_text(
        (
            f'<testsuite tests="580" failures="0" errors="0" skipped="{skipped}">'
            + "".join(cases)
            + "</testsuite>"
        ),
        encoding="utf-8",
    )


def test_commercial_release_versions_and_documented_claim_policy_are_exact() -> None:
    assert set(_unified_versions(REPOSITORY).values()) == {"0.7.0"}
    assert all(_docs(REPOSITORY).values())
    assert COMPETITIVE_EVIDENCE_MISSING == [
        "real_model_task_e2e",
        "named_baseline_results_17",
        "secret_held_out_results",
        "independent_evaluator_signatures",
    ]


def test_commercial_manifest_schema_cannot_reverse_owner_decision() -> None:
    schema = json.loads(
        (
            REPOSITORY / "contracts/commercial-release-manifest.v1.schema.json"
        ).read_text(encoding="utf-8")
    )
    Draft202012Validator.check_schema(schema)
    properties = schema["properties"]
    assert properties["commercial_release_eligible"] == {"const": True}
    assert properties["competitive_claim_eligible"] == {"const": False}
    assert set(properties["competitive_evidence_missing"]["items"]["enum"]) == set(
        COMPETITIVE_EVIDENCE_MISSING
    )


def test_release_reports_are_content_digest_bound(tmp_path: Path) -> None:
    path = tmp_path / "report.json"
    write_report(path, {"schema_version": "test/v1", "passed": True})
    report = json.loads(path.read_text(encoding="utf-8"))
    verify_record_digest(report, field="test report")
    report["passed"] = False
    with pytest.raises(RuntimeError, match="digest is invalid"):
        verify_record_digest(report, field="test report")


def test_platform_gate_accepts_no_skip_suite_and_rejects_a_skip(tmp_path: Path) -> None:
    passed = tmp_path / "passed.xml"
    _junit(passed)
    report = _junit_report(passed, expected_system="Windows")
    assert report["tests"] == 580
    assert report["skipped"] == 0
    assert report["windows_native_observed"] is True

    skipped = tmp_path / "skipped.xml"
    _junit(skipped, skipped=1)
    with pytest.raises(PlatformGateError, match="zero failures, errors, and skips"):
        _junit_report(skipped, expected_system="Windows")


def test_platform_gate_uses_utf8_for_cross_platform_subprocess_output() -> None:
    workflow = (REPOSITORY / ".github/workflows/commercial-gates.yml").read_text(
        encoding="utf-8"
    )

    assert 'PYTHONUTF8: "1"' in workflow


def test_release_oci_contract_is_non_root_and_has_no_listener() -> None:
    dockerfile = (REPOSITORY / "packaging/oci/Dockerfile").read_text(encoding="utf-8")
    assert "USER 65532:65532" in dockerfile
    assert 'ENTRYPOINT ["deeplaw"]' in dockerfile
    assert 'CMD ["--version"]' in dockerfile
    assert "deeplaw-0.7.0-py3-none-any.whl" in dockerfile
    assert "/tmp/deeplaw.whl" not in dockerfile
    assert "EXPOSE " not in dockerfile


def test_release_workflow_resumes_without_overwriting_published_assets() -> None:
    workflow = (REPOSITORY / ".github/workflows/release.yml").read_text(encoding="utf-8")

    validation = workflow.split(
        "      - name: Create and verify Sigstore OIDC bundles", maxsplit=1
    )[0]
    assert 'for path in sorted(assets_root.rglob("*")):' in validation
    assert 'raise SystemExit(f"duplicate release asset basename: {path.name}")' in validation
    assert 'files_by_name.get(name)' in validation
    assert 'hashlib.sha256(path.read_bytes()).hexdigest()' in validation
    assert 'expected_names = set(files_by_name) - {checksum_path.name}' in validation
    assert 'raise SystemExit("release checksum inventory is incomplete")' in validation
    assert "sha256sum --check SHA256SUMS" not in validation
    assert "Create or resume the release without overwriting assets" in workflow
    assert "Attach or verify post-release evidence without overwriting assets" in workflow
    assert 'releases?per_page=100' in workflow
    assert "release_id=$(jq -r '.id'" in workflow
    assert (
        "uploads.github.com/repos/${GITHUB_REPOSITORY}/releases/${release_id}/assets"
        in workflow
    )
    assert 'repos/${GITHUB_REPOSITORY}/releases/${RELEASE_ID}' in workflow
    assert workflow.count('remote_digest=$(jq -r --arg name "${name}"') == 2
    assert '[[ "${remote_digest}" == "${local_digest}" ]]' in workflow
    assert workflow.count('gh release upload "${RELEASE_TAG}" "${asset}"') == 1
    assert "--clobber" not in workflow
