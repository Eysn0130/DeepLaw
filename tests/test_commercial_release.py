from __future__ import annotations

import json
import tomllib
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
    project = tomllib.loads((REPOSITORY / "pyproject.toml").read_text(encoding="utf-8"))
    assert set(_unified_versions(REPOSITORY).values()) == {project["project"]["version"]}
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


def test_homepages_keep_history_out_of_current_status_and_link_navigation() -> None:
    historical_name = "LIVING_WIKI_ACCEPTANCE_REPORT_2026-07-30.md"
    historical = (REPOSITORY / "docs" / historical_name).read_text(encoding="utf-8")
    assert "Historical pre-release implementation report" in historical
    assert "not the\n> v0.11.0 release decision" in historical

    for homepage_name in ("README.md", "README_EN.md"):
        homepage = (REPOSITORY / homepage_name).read_text(encoding="utf-8")
        assert "docs/README.md" in homepage
        assert "0.12.0 Beta" in homepage
        assert "machine_evaluation_pending" in homepage
        assert "V0_12_ACCEPTANCE_MATRIX.md" not in homepage
        assert "RELEASE_NOTES_v0.12.0.md" not in homepage
        assert "commercial-release-manifest.json" not in homepage
        assert "post-release-verification.json" not in homepage
        assert historical_name not in homepage


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
    semantic_quality = properties["semantic_living_wiki_quality"]
    assert "formal_release_eligible" in semantic_quality["required"]
    assert semantic_quality["properties"]["formal_release_eligible"] == {"const": True}
    assembler = (REPOSITORY / "benchmarks/release/commercial_release.py").read_text(
        encoding="utf-8"
    )
    assert '"formal_release_eligible": True' in assembler


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
    assert 'version: "0.11.5"' in workflow
    assert workflow.count('version: "0.11.5"') == workflow.count("astral-sh/setup-uv@")


def test_pull_request_gates_check_out_the_exact_head_commit() -> None:
    commercial = (REPOSITORY / ".github/workflows/commercial-gates.yml").read_text(
        encoding="utf-8"
    )
    ci = (REPOSITORY / ".github/workflows/ci.yml").read_text(encoding="utf-8")

    exact_commercial_ref = (
        "ref: ${{ inputs.release_ref || github.event.pull_request.head.sha || github.sha }}"
    )
    exact_ci_ref = "ref: ${{ github.event.pull_request.head.sha || github.sha }}"
    assert commercial.count(exact_commercial_ref) == 7
    assert "ref: ${{ inputs.release_ref || github.sha }}" not in commercial
    assert ci.count(exact_ci_ref) == 2
    assert "  pull_request:" not in commercial
    assert "qualification and not windows_native" in commercial
    assert 'marker: not qualification' in commercial
    assert "--manifest" in commercial
    assert "--binding-receipt" in commercial


def test_candidate_ci_is_current_source_regression_not_release_readiness() -> None:
    ci = (REPOSITORY / ".github/workflows/ci.yml").read_text(encoding="utf-8")
    candidate = (REPOSITORY / ".github/workflows/candidate-full.yml").read_text(
        encoding="utf-8"
    )

    assert "name: Fast PR" in ci
    assert "timeout-minutes: 20" in ci
    assert "Ubuntu Python 3.12" in ci
    assert "windows-sentinel" in ci
    assert "uv lock --check" in ci
    assert "ruff check ." in ci
    assert "git diff --check" in ci
    assert 'git diff --check "${{ github.event.pull_request.base.sha }}...HEAD"' in ci
    assert "fetch-depth: 0" in ci

    assert "runs-on: ${{ matrix.os }}" in candidate
    assert all(
        system in candidate
        for system in ("ubuntu-latest", "macos-latest", "windows-latest")
    )
    assert all(version in candidate for version in ('"3.11"', '"3.12"', '"3.13"'))
    assert "fail-fast: false" in candidate
    assert "duration shard ${{ matrix.shard }} of 3" in candidate
    assert "benchmarks.release.candidate_regression" in candidate
    assert "Verify complete disjoint Windows" in candidate
    assert "candidate-skip-receipt.json" in candidate
    assert "windows-duration-weights.json" in candidate

    aggregate = candidate.split("  windows-aggregate:", 1)[1]
    assert "setup-uv" not in aggregate
    assert "python -m benchmarks.release.candidate_regression" in aggregate
    assert "--require-eligible" not in candidate
    assert "tests/test_v013_pass21_release_closure.py" in ci


def test_manual_platform_core_preflight_is_fail_closed_before_core_execution() -> None:
    gate = (REPOSITORY / ".github/workflows/commercial-gates.yml").read_text(
        encoding="utf-8"
    )
    preflight = gate.index("Preflight frozen Platform Core collection")
    core = gate.index("Run full mandatory suite with zero skips")
    assert preflight < core
    assert "benchmarks.release.platform_inventory" in gate
    assert "--mode platform_core" in gate
    assert "--require-match" in gate
    assert "platform-core-inventory-preflight-${{ matrix.slug }}.json" in gate
    assert "platform-evidence/platform-core-inventory-preflight-${{ matrix.slug }}.json" in gate


def test_release_gate_runs_protocol_from_exact_wheel_and_publishes_evidence() -> None:
    gate = (REPOSITORY / ".github/workflows/commercial-gates.yml").read_text(encoding="utf-8")
    release = (REPOSITORY / ".github/workflows/release.yml").read_text(encoding="utf-8")

    assert "evaluation-protocol:" in gate
    assert "--candidate-wheel" in gate
    assert gate.count("--require-eligible") >= 2
    assert "--verify-report-dir" in gate
    assert 'test -z "$(git status --porcelain=v1 --untracked-files=all)"' in gate
    assert "--evaluation" in gate
    assert "python -m benchmarks.release.release_provenance_v8" in release
    publish = release.split("\n  publish:", maxsplit=1)[1].split(
        "\n  public-redownload:", maxsplit=1
    )[0]
    public_redownload = release.split("\n  public-redownload:", maxsplit=1)[1]
    assert "deeplaw.commercial-release-manifest/v5" not in publish
    assert "deeplaw.commercial-release-manifest/v5" not in public_redownload
    assert "qualification_run_id" in release
    assert "candidate_run_id" in release
    assert "commercial-release-assets" in release
    assert "verified-candidate-artifacts" in release
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
    assert "benchmarks.release.release_provenance_v8" in release
    assert "benchmarks.release.external_qualification_bundle_v4" in release
    assert "--candidate-machine-reference-binding" in release
    assert "post_build_machine_reference_binding" in release
    assert "public_release_verified" in release
    assert "release_provenance_v7" not in release
    assert "benchmarks.release.retained_artifact_manifest" in release


def test_living_wiki_quality_uploads_partial_evidence_after_scorer_failure() -> None:
    workflow = (REPOSITORY / ".github/workflows/commercial-gates.yml").read_text(
        encoding="utf-8"
    )
    quality = workflow.split("  living-wiki-quality:", maxsplit=1)[1].split(
        "  assemble:", maxsplit=1
    )[0]
    upload = quality.split(
        "      - name: Upload Living Wiki quality evidence", maxsplit=1
    )[1]
    scorer = quality.split(
        "      - name: Run same-condition baseline and exact-wheel quality suites",
        maxsplit=1,
    )[1].split("      - name: Upload Living Wiki quality evidence", maxsplit=1)[0]

    assert "if: ${{ always() }}" in upload
    assert "if-no-files-found: error" in upload
    assert "living-wiki-quality-baseline.json" in upload
    assert "living-wiki-quality-report.json" in upload
    assert "living-wiki-quality-comparison.json" in upload
    # The quality scorer and comparison remain fail-closed; only evidence
    # publication is unconditional after a failure.
    candidate_scorer = scorer.split("          candidate_runtime=", maxsplit=1)[1]
    assert "continue-on-error: true" not in scorer
    assert "--allow-fail" not in candidate_scorer


def test_semantic_release_evidence_uses_deterministic_machine_consensus() -> None:
    workflow = (REPOSITORY / ".github/workflows/semantic-evidence.yml").read_text(encoding="utf-8")
    release = (REPOSITORY / ".github/workflows/release.yml").read_text(encoding="utf-8")
    assembler = (REPOSITORY / "benchmarks/release/commercial_release.py").read_text(
        encoding="utf-8"
    )
    lifecycle_schema = json.loads(
        (
            REPOSITORY
            / "contracts/deterministic-semantic-lifecycle.v1.schema.json"
        ).read_text(encoding="utf-8")
    )

    assert "if: ${{ inputs.mode == 'package_consensus' }}" in workflow
    assert "secrets.OPENAI_API_KEY" not in workflow
    assert "owner-approved release scope" in workflow
    assert "build_machine_review_consensus" in workflow
    assert "packet_count=$((packet_count + 1))" in workflow
    assert 'test "${packet_count}" -eq 6' in workflow
    assert 'test "${#packet_arguments[@]}" -eq 6' not in workflow
    assert "v0.12-28-source-decision-matrix.json" in workflow
    assert "semantic-release-evidence" in workflow
    assert "human_final_decision" not in workflow
    assert "reviewer_id" not in workflow
    assert "semantic-review-vault.tar.gz" not in workflow
    assert "external_model_execution" not in workflow
    assert "commercial-qualification.yml" in release
    assert "semantic-release-evidence" not in release
    assert lifecycle_schema["properties"]["formal_release_evidence_ready"] == {
        "const": False
    }
    assert (
        'lifecycle.get("formal_release_evidence_ready") is False'
        in assembler
    )
    assert (
        'lifecycle.get("formal_release_evidence_ready") is not True'
        not in assembler
    )
    assert "Machine review packet does not bind first-party query evidence" not in assembler


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

    assert (
        "Create or resume the draft or public prerelease without overwriting assets"
        in workflow
    )
    assert "cmp --silent" in workflow
    assert "published release asset differs" in workflow
    assert "--clobber" not in workflow
    assert "Publicly redownload immutable release without credentials" in workflow
    anonymous = workflow.split(
        "      - name: Publicly redownload immutable release without credentials",
        maxsplit=1,
    )[1].split("      - name:", maxsplit=1)[0]
    assert "GH_TOKEN: ${{ github.token }}" not in anonymous
    assert "curl --fail --location" in workflow
    assert "sha256sum --check SHA256SUMS" in workflow
    assert "sigstore/gh-action-sigstore-python" in workflow
    assert '"${artifact}.sigstore.json"' in workflow
    assert "uvx --from sigstore==4.3.0 sigstore verify identity" in workflow
    assert "gh attestation verify" in workflow
    assert "repos/${GITHUB_REPOSITORY}/releases/${release_id}/assets" in workflow
    assert "repos/${GITHUB_REPOSITORY}/releases/assets/${asset_id}" in workflow
    assert 'gh release upload "${RELEASE_TAG}" "${artifact}"' in workflow
    assert "--clobber" not in workflow
