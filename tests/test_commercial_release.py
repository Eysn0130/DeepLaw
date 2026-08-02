from __future__ import annotations

import json
from pathlib import Path

import pytest
from jsonschema import Draft202012Validator

from benchmarks.release.commercial_release import (
    COMPETITIVE_EVIDENCE_MISSING,
    CommercialReleaseError,
    _docs,
    _source_quality_matrix,
    _unified_versions,
)
from benchmarks.release.evidence import repository_binding, verify_record_digest, write_report
from benchmarks.release.platform_gate import (
    REQUIRED_TEST_MODULES,
    WINDOWS_NATIVE_TESTS,
    PlatformGateError,
    _junit_report,
)
from deeplaw.catalog_signing import verify_catalog_signature

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


def test_release_versions_public_homepages_and_claim_policy_are_exact() -> None:
    assert set(_unified_versions(REPOSITORY).values()) == {"0.12.0"}
    assert all(_docs(REPOSITORY).values())
    assert "商业" not in (REPOSITORY / "README.md").read_text(encoding="utf-8")
    english_homepage = (REPOSITORY / "README_EN.md").read_text(encoding="utf-8")
    assert "commercial" not in english_homepage.casefold().replace(
        "commercial-release-manifest.json", "release-manifest.json"
    )
    assert COMPETITIVE_EVIDENCE_MISSING == [
        "named_baseline_results_17",
        "paired_confidence_intervals",
        "comparative_failure_and_cost_inventory",
    ]


def test_homepages_distinguish_historical_and_formal_release_evidence() -> None:
    historical_name = "LIVING_WIKI_ACCEPTANCE_REPORT_2026-07-30.md"
    historical = (REPOSITORY / "docs" / historical_name).read_text(encoding="utf-8")
    assert "Historical pre-release implementation report" in historical
    assert "not the\n> v0.11.0 release decision" in historical

    for homepage_name in ("README.md", "README_EN.md"):
        homepage = (REPOSITORY / homepage_name).read_text(encoding="utf-8")
        assert "V0_12_ACCEPTANCE_MATRIX.md" in homepage
        assert "RELEASE_NOTES_v0.12.0.md" in homepage
        assert "commercial-release-manifest.json" in homepage
        assert "post-release-verification.json" in homepage
        if historical_name in homepage:
            historical_line = next(
                line for line in homepage.splitlines() if historical_name in line
            ).casefold()
            assert "histor" in historical_line or "pre-release" in historical_line


def test_commercial_manifest_schema_cannot_reverse_owner_decision() -> None:
    schema = json.loads(
        (REPOSITORY / "contracts/commercial-release-manifest.v5.schema.json").read_text(
            encoding="utf-8"
        )
    )
    Draft202012Validator.check_schema(schema)
    properties = schema["properties"]
    assert properties["commercial_release_eligible"] == {"const": True}
    assert properties["quality_protocol_eligible"] == {"const": True}
    assert properties["competitive_claim_eligible"] == {"const": False}
    assert set(properties["competitive_evidence_missing"]["items"]["enum"]) == set(
        COMPETITIVE_EVIDENCE_MISSING
    )
    assert {
        "semantic_living_wiki_quality",
        "authoritative_evidence_quality",
        "editor_integrations",
    } <= set(schema["required"])


def test_authoritative_source_matrix_binds_exact_signed_catalog(
    tmp_path: Path,
) -> None:
    binding = repository_binding(REPOSITORY)
    artifact_sha256 = "a" * 64
    catalog_path = REPOSITORY / "catalogs/deeplaw-official-cn.json"
    signature_path = REPOSITORY / "catalogs/deeplaw-official-cn.json.sig"
    catalog = json.loads(catalog_path.read_text(encoding="utf-8"))
    signature = verify_catalog_signature(
        catalog_path.read_bytes(),
        signature_path.read_bytes(),
        trust_store_path=REPOSITORY / "trust/official-catalog-keys.v1.json",
    )
    sources = []
    for index, document in enumerate(catalog["documents"], start=1):
        sources.append(
            {
                "stable_source_id": f"doc_{index:024x}",
                "source_revision_id": None,
                "source_revision_semantics": "Signed Authoritative Pack document identity.",
                "immutable_bytes_sha256": document["sha256"],
                "immutable_bytes_verified": True,
                "byte_size": document["byteSize"],
                "format": document["format"],
                "media_type": (
                    "application/pdf"
                    if document["format"] == "PDF"
                    else "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
                ),
                "lifecycle": "active",
                "scope": "law_support_official",
                "sensitivity": "public",
                "origin": "official",
                "authority": "official",
                "parser": {"identity": "fixture"},
                "source_ir": {"digest": "b" * 64},
                "fragments": {"count": 1},
                "extraction_quality": {"coverage_ratio": 1},
                "compilation": {"domain": "authoritative_pack_release_build"},
                "knowledge_output": {"legal_authority": True},
                "derived_state": {"fts": "ready"},
                "authoritative_pack": {"signature_verified": True},
                "retrieval_probe": {"citation_valid": True},
                "decision": "no_action",
                "reason_codes": ["bytes_verified", "citation_verified"],
                "execution_status": "verified",
                "rollback": {"restore_verified": True},
            }
        )
    matrix_path = tmp_path / "matrix.json"
    write_report(
        matrix_path,
        {
            "schema_version": "deeplaw.authoritative-source-quality-decision-matrix/v2",
            "release_target": "0.12.0",
            "status": "executed_and_verified",
            "candidate_binding": {
                "commit": binding["commit"],
                "tree": binding["tree"],
                "version": binding["package_version"],
                "artifact_sha256": artifact_sha256,
            },
                "catalog": {
                    "sha256": signature["catalog_sha256"],
                    "signature_sha256": signature["signature_sha256"],
                    "signature_key_id": signature["key_id"],
                },
            "snapshot": {"restore_verified": True},
            "rebuild": {"external_model_used": False},
            "reproducibility": {
                "isolated_build_count": 2,
                "database_byte_identical": True,
                "build_report_identical": True,
                "query_result_semantics_identical": True,
            },
            "retrieval_quality": {"quality_regression": False},
            "security": {
                "unauthorized_disclosure": 0,
                "restricted_disclosure": 0,
                "unauthorized_mutation": 0,
                "silent_fallback": 0,
                "stale_prohibited_selection": 0,
                "invalid_official_citation": 0,
                "unsupported_authoritative_claim": 0,
                "authority_elevation": 0,
                "provider_hard_limit_violation": 0,
                "challenge_attempt_count": 10,
                "challenge_execution": {
                    "unauthorized_disclosure": True,
                    "restricted_disclosure": True,
                    "silent_fallback": True,
                    "unsupported_authoritative_claim": True,
                    "tampered_receipt_rejected": True,
                    "tampered_signature_rejected": True,
                    "signed_catalog_rollback_rejected": True,
                    "law_support_mutation_rejected": True,
                    "authority_elevation": True,
                    "prompt_injection": True,
                },
            },
            "decision_summary": {
                "no_action": 28,
                "rebuild_derived": 0,
                "recompile_knowledge": 0,
                "reparse_source_ir": 0,
                "ingest_new_source_revision": 0,
                "blocked_invalid_evidence": 0,
            },
            "sources": sources,
            "active_release": {
                "release_id": "lawrel_" + "c" * 32,
                "verified": True,
            },
            "limitations": ["one", "two", "three", "four"],
            "external_real_model_semantic_execution": "not_executed",
            "competitive_claim_eligible": False,
        },
    )
    matrix = _source_quality_matrix(
        REPOSITORY,
        matrix_path,
        binding=binding,
        artifact_sha256=artifact_sha256,
    )
    assert len(matrix["sources"]) == 28

    tampered = json.loads(matrix_path.read_text(encoding="utf-8"))
    tampered["sources"][0]["immutable_bytes_sha256"] = "0" * 64
    tampered_path = tmp_path / "tampered-matrix.json"
    write_report(tampered_path, tampered)
    with pytest.raises(CommercialReleaseError, match="matrix is incomplete"):
        _source_quality_matrix(
            REPOSITORY,
            tampered_path,
            binding=binding,
            artifact_sha256=artifact_sha256,
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
    workflow = (REPOSITORY / ".github/workflows/commercial-gates.yml").read_text(encoding="utf-8")

    assert 'PYTHONUTF8: "1"' in workflow


def test_release_gate_runs_protocol_from_exact_wheel_and_publishes_evidence() -> None:
    gate = (REPOSITORY / ".github/workflows/commercial-gates.yml").read_text(encoding="utf-8")
    release = (REPOSITORY / ".github/workflows/release.yml").read_text(encoding="utf-8")

    assert "evaluation-protocol:" in gate
    assert "--candidate-wheel" in gate
    assert gate.count("--require-eligible") >= 2
    assert "--verify-report-dir" in gate
    assert 'test -z "$(git status --porcelain=v1 --untracked-files=all)"' in gate
    assert "--evaluation" in gate
    assert "evaluation/EVALUATION_SHA256SUMS" in release
    assert "deeplaw.commercial-release-manifest/v5" in release
    assert "semantic_evidence_run_id" in release
    assert "semantic-release-evidence" in gate
    assert "--semantic-consensus" in gate
    assert gate.count("--semantic-machine-review") == 6
    assert "--semantic-owner-review-chinese" in gate
    assert "benchmarks.legal.run_authoritative_evidence_gate" in gate
    assert "--authoritative-evidence-quality" in gate
    assert "quality/authoritative-evidence-quality.json" in gate
    assert "living-wiki-quality:" in gate
    assert "--living-wiki-quality" in gate
    assert "--living-wiki-baseline" in gate
    assert "--living-wiki-comparison" in gate
    assert "42382b264f4297965c25aaac6e85619e9e0d49b7" in gate
    assert "9bda60831e4380092c9a3bdb80103b5ec8abbf5a2be0adf6ffd57f61cfa46ca0" in gate
    assert "benchmarks.living_wiki.compare_quality" in gate
    assert "--source-quality-matrix" in gate
    assert gate.count('python: "3.11"') == 3
    assert gate.count('python: "3.12"') == 3
    assert gate.count('python: "3.13"') == 3
    assert "--expected-python" in gate
    assert 'manifest.get("quality_protocol_eligible") is not True' in release


def test_semantic_release_evidence_uses_deterministic_machine_consensus() -> None:
    workflow = (REPOSITORY / ".github/workflows/semantic-evidence.yml").read_text(encoding="utf-8")
    release = (REPOSITORY / ".github/workflows/release.yml").read_text(encoding="utf-8")

    assert "if: ${{ inputs.mode == 'package_consensus' }}" in workflow
    assert "secrets.OPENAI_API_KEY" not in workflow
    assert "owner-approved release scope" in workflow
    assert "build_machine_review_consensus" in workflow
    assert "v0.12-28-source-decision-matrix.json" in workflow
    assert "semantic-release-evidence" in workflow
    assert "human_final_decision" not in workflow
    assert "reviewer_id" not in workflow
    assert "semantic-review-vault.tar.gz" not in workflow
    assert "external_model_execution" not in workflow
    assert "semantic_evidence_run_id" in release
    assert "semantic-release-evidence" in release


def test_release_oci_contract_is_non_root_and_has_no_listener() -> None:
    dockerfile = (REPOSITORY / "packaging/oci/Dockerfile").read_text(encoding="utf-8")
    assert "USER 65532:65532" in dockerfile
    assert 'ENTRYPOINT ["deeplaw"]' in dockerfile
    assert 'CMD ["--version"]' in dockerfile
    assert "COPY deeplaw-*.whl /tmp/" in dockerfile
    assert "set -- /tmp/deeplaw-*.whl" in dockerfile
    assert '--no-deps "$1"' in dockerfile
    assert "EXPOSE " not in dockerfile


def test_release_workflow_resumes_without_overwriting_published_assets() -> None:
    workflow = (REPOSITORY / ".github/workflows/release.yml").read_text(encoding="utf-8")

    validation = workflow.split(
        "      - name: Create and verify Sigstore OIDC bundles", maxsplit=1
    )[0]
    assert 'for path in sorted(assets_root.rglob("*")):' in validation
    assert 'raise SystemExit(f"duplicate release asset basename: {path.name}")' in validation
    assert "files_by_name.get(name)" in validation
    assert "hashlib.sha256(path.read_bytes()).hexdigest()" in validation
    assert "expected_names = set(files_by_name) - {checksum_path.name}" in validation
    assert 'raise SystemExit("release checksum inventory is incomplete")' in validation
    assert "sha256sum --check SHA256SUMS" not in validation
    assert "Create or resume the release without overwriting assets" in workflow
    assert "Attach or verify post-release evidence without overwriting assets" in workflow
    assert "post_release_only" in workflow
    assert "needs.publish.result == 'success'" in workflow
    assert "verified provenance: %s" in workflow
    assert "verified CycloneDX SBOM: %s" in workflow
    assert "uvx --from sigstore==4.3.0 sigstore sign" in workflow
    assert "uvx --from sigstore==4.3.0 sigstore verify identity" in workflow
    assert '--cert-identity "${RELEASE_CERT_IDENTITY}"' in workflow
    assert (
        "https://github.com/${{ github.repository }}/.github/workflows/release.yml@refs/tags/"
        in workflow
    )
    assert "required_signed_assets=(" in workflow
    assert '"commercial-release-manifest.json"' in workflow
    assert '"SHA256SUMS"' in workflow
    assert "-name '*.sigstore.json' -print0 | sort -z" in workflow
    assert 'artifact="${bundle%.sigstore.json}"' in workflow
    assert "verified_bundle_count=$((verified_bundle_count + 1))" in workflow
    assert "releases?per_page=100" in workflow
    assert "for attempt in 1 2 3 4 5; do" in workflow
    assert 'if [[ "${attempt}" -eq 5 ]]; then' in workflow
    assert "draft release was not observable after bounded retries" in workflow
    assert "release_id=$(jq -r '.id'" in workflow
    assert "uploads.github.com/repos/${GITHUB_REPOSITORY}/releases/${release_id}/assets" in workflow
    assert "repos/${GITHUB_REPOSITORY}/releases/${RELEASE_ID}" in workflow
    assert "{tag_name, target_commitish, name, body, draft, prerelease, make_latest}" in workflow
    assert workflow.count('remote_digest=$(jq -r --arg name "${name}"') == 2
    assert '[[ "${remote_digest}" == "${local_digest}" ]]' in workflow
    assert workflow.count('gh release upload "${RELEASE_TAG}" "${asset}"') == 1
    assert "--clobber" not in workflow
