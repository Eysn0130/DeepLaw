from __future__ import annotations

from pathlib import Path

import yaml

REPOSITORY = Path(__file__).resolve().parents[1]


def _workflow(name: str) -> str:
    return (REPOSITORY / ".github" / "workflows" / name).read_text(encoding="utf-8")


def test_candidate_full_retains_raw_platform_and_exact_wheel_evidence() -> None:
    workflow = _workflow("candidate-full.yml")

    assert (
        "uv export --frozen --no-dev --no-emit-project --no-emit-local"
        in workflow
    )
    assert "--no-sources" not in workflow
    assert "candidate-requirements.txt" in workflow
    assert "benchmarks.release.exact_wheel_runner" in workflow
    assert "--receipt-contract candidate-full-v1" in workflow
    assert "exact-wheel-execution.json" in workflow
    assert "candidate-full-raw-evidence" in workflow
    assert "benchmarks.release.pre_publish_artifact_gate" in workflow
    assert "pre-publish-artifact-gate.json" in workflow
    assert "deeplaw.cdx.json" in workflow
    assert "installed-licenses.json" in workflow
    assert "security/openvex.json" in workflow
    assert "candidate-tests.xml" in workflow
    assert "windows-calibration.xml" in workflow
    assert "windows-calibration-shards:" in workflow
    assert "windows-calibration-shard-${{ matrix.shard }}" in workflow
    assert "windows-calibration-aggregate.json" in workflow
    assert "windows-aggregate.json" in workflow
    assert "--junit-output" in workflow
    assert "--output-file candidate-requirements.txt" in workflow
    assert 'export_dir="$(mktemp -d ' in workflow
    assert 'UV_PROJECT="${GITHUB_WORKSPACE}" \\' in workflow
    assert 'test ! -e "${destination}"' in workflow
    assert 'test ! -L "${destination}"' in workflow
    assert 'test ! -L "${source}"' in workflow
    assert 'mv "${source}" "${destination}"' in workflow
    assert '--output-file "${RUNNER_TEMP}' not in workflow
    assert "benchmarks.release.candidate_artifact_path_policy" in workflow
    assert workflow.count("normalize-junit") >= 5
    assert workflow.count('--checkout-root "${GITHUB_WORKSPACE}"') >= 5
    assert "--root \"${RUNNER_TEMP}/candidate-full-raw-evidence\"" in workflow
    assert (
        '--requirements "${RUNNER_TEMP}/candidate-full-raw-evidence/'
        'verified-candidate-artifacts/candidate-requirements.txt"'
        in workflow
    )
    assert workflow.index(
        "Validate retained Candidate Full text and XML before inventory upload"
    ) < workflow.index(
        "Write path-independent raw evidence inventory receipt"
    )
    assert "= 14" in workflow
    assert "candidate_regression platform" in workflow
    assert "platform-matrix-receipt.json" in workflow
    assert "platform-core-test-manifest-v2.json" in workflow
    assert "retention-days: 90" in workflow


def test_external_qualification_consumes_candidate_full_and_emits_typed_evidence() -> None:
    workflow = _workflow("external-qualification-evidence.yml")

    assert "candidate-full-raw-evidence" in workflow
    assert "verified-candidate-artifacts" in workflow
    assert "benchmarks.release.exact_wheel_runner" in workflow
    assert "--receipt-contract external-qualification-v2" in workflow
    assert "benchmarks.release.external_qualification_bundle_v4" in workflow
    assert "machine-only v4" in workflow
    assert "DEEPLAW_REFERENCE_FREEZER" in workflow
    assert "DEEPLAW_REVIEWER_OUTPUT_1" in workflow
    assert "DEEPLAW_CANDIDATE_HOST_RUNNER" in workflow
    assert "DEEPLAW_CODEX_CREDENTIAL_BROKER" in workflow
    assert "DEEPLAW_OPENCODE_CREDENTIAL_BROKER" in workflow
    assert "DEEPLAW_SCORER_A" in workflow
    assert "DEEPLAW_SCORER_B" in workflow
    assert "DEEPLAW_DETERMINISTIC_ARBITER" in workflow
    assert "--candidate-run-id" in workflow
    assert "--evidence-run-id" in workflow
    assert "--qualification-run-id" not in workflow
    assert "DEEPLAW_HUMAN_GOLD_ROOT" not in workflow
    assert "--trusted-human-approver" not in workflow
    assert "python -m build" not in workflow
    assert "uv build" not in workflow
    assert "hatch build" not in workflow


def test_legacy_semantic_evidence_is_manual_only_and_stays_pre_v013() -> None:
    workflow = _workflow("semantic-evidence.yml")
    parsed = yaml.safe_load(workflow)
    triggers = parsed.get("on", parsed.get(True))

    assert set(triggers) == {"workflow_dispatch"}
    inputs = triggers["workflow_dispatch"]["inputs"]
    assert inputs["mode"]["type"] == "choice"
    assert inputs["mode"]["options"] == ["deterministic_review", "package_consensus"]
    assert inputs["mode"]["default"] == "deterministic_review"
    assert inputs["release_ref"]["required"] is True
    assert inputs["evidence_ref"]["required"] is False
    assert "Legacy pre-v0.13 Semantic Living Wiki evidence" in parsed["name"]
    assert "Gate v6" not in workflow
    guard = 'if tuple(map(int, version.split("."))) >= (0, 13, 0):'
    rejection = "v0.13 must use the active qualification and Gate v9 path"
    assert workflow.count(guard) == 2
    assert workflow.count(rejection) == 2


def test_commercial_qualification_recomputes_current_typed_core_gates() -> None:
    workflow = _workflow("commercial-qualification.yml")

    assert "verified-candidate-artifacts" in workflow
    assert "kernel-qualification-evidence" in workflow
    assert "benchmarks.release.kernel_qualification_bundle_v1" in workflow
    assert "benchmarks.release.assemble_commercial_qualification_v9" in workflow
    assert "benchmarks.release.release_provenance_v9" in workflow
    assert "candidate_run_id" in workflow
    assert "evidence_run_id" in workflow
    assert 'QUALIFICATION_RUN_ID: ${{ github.run_id }}' in workflow
    assert '--qualification-run-id "${QUALIFICATION_RUN_ID}"' in workflow
    assert "Kernel Release Core" in workflow
    assert "post_build_machine_reference_binding" not in workflow
    assert "candidate_machine_reference" not in workflow
    assert "benchmarks.release.assemble_commercial_qualification_v7" not in workflow
    assert "benchmarks.release.release_provenance_v7" not in workflow


def test_release_is_manual_three_run_provenance_then_draft_and_public_verify() -> None:
    workflow = _workflow("release.yml")
    parsed = yaml.safe_load(workflow)
    triggers = parsed.get("on", parsed.get(True))

    assert set(triggers) == {"workflow_dispatch"}
    inputs = triggers["workflow_dispatch"]["inputs"]
    assert {
        "release_tag",
        "candidate_run_id",
        "evidence_run_id",
        "qualification_run_id",
        "owner_release_confirmation",
    } <= set(inputs)
    assert "test \"${OWNER_RELEASE_CONFIRMATION}\" = \"publish-v0.13.0\"" in workflow
    assert "benchmarks.release.release_provenance_v9" in workflow
    assert "benchmarks.release.kernel_qualification_bundle_v1" in workflow
    assert "--candidate-run-id" in workflow
    assert "--evidence-run-id" in workflow
    assert "--qualification-run-id" in workflow
    assert (
        'gh release create "${RELEASE_TAG}" --verify-tag --draft --prerelease '
        '--title "${RELEASE_TITLE}" --notes "${RELEASE_NOTES}"'
    ) in workflow
    assert "gh release edit \"${RELEASE_TAG}\" --draft=false" in workflow
    assert "Publicly redownload immutable release without credentials" in workflow
    assert "post-public-verification.json" in workflow
    assert workflow.index("--draft") < workflow.index("--draft=false")
    assert workflow.index("--draft=false") < workflow.index(
        "Publicly redownload immutable release without credentials"
    )
    assert "post_build_machine_reference_binding" not in workflow
    assert "--candidate-machine-reference-binding" not in workflow
    assert "public_release_verified" in workflow
    assert "release_provenance_v7" not in workflow
    assert 'tag_object_sha: ${{ steps.release.outputs.tag_object_sha }}' in workflow
    assert 'git cat-file -t "${TARGET_TAG}"' in workflow
    assert 'test "$(git cat-file -t "${TARGET_TAG}")" = tag' in workflow
    assert 'git rev-parse "${TARGET_TAG}^{commit}"' in workflow
    assert 'git/ref/tags/${TARGET_TAG}' in workflow
    assert 'test "${remote_tag_type}" = tag' in workflow
    assert 'test "${remote_tag_sha}" = "${tag_object_sha}"' in workflow
    assert (
        "DeepLaw 0.13.0 Beta — machine-evaluated technical release" in workflow
    )
    assert (
        "Machine-evaluated Beta release; no Human Gold, human review, legal expert review, "
        "or legal authority attestation is claimed."
        in workflow
    )
    assert '--title "${RELEASE_TITLE}"' in workflow
    assert '--notes "${RELEASE_NOTES}"' in workflow
    assert 'gh release view "${RELEASE_TAG}" --json name --jq .name' in workflow
    assert 'gh release view "${RELEASE_TAG}" --json body --jq .body' in workflow
    assert 'gh release view "${RELEASE_TAG}" --json isPrerelease --jq .isPrerelease' in workflow
    assert workflow.count('test "${remote_tag_sha}" = "${RELEASE_TAG_OBJECT_SHA}"') >= 3
    assert workflow.count('--json name --jq .name') >= 3
    assert workflow.count('--json body --jq .body') >= 3
    assert "--prerelease=false" not in workflow
    assert (
        'test "$(gh release view "${RELEASE_TAG}" --json isDraft --jq .isDraft)" = false'
        in workflow
    )
    assert (
        'test "$(gh release view "${RELEASE_TAG}" --json isPrerelease --jq .isPrerelease)" = true'
        in workflow
    )
