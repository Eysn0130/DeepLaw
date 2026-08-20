from __future__ import annotations

from pathlib import Path

import yaml

REPOSITORY = Path(__file__).resolve().parents[1]


def _workflow(name: str) -> str:
    return (REPOSITORY / ".github" / "workflows" / name).read_text(encoding="utf-8")


def test_candidate_full_retains_raw_platform_and_exact_wheel_evidence() -> None:
    workflow = _workflow("candidate-full.yml")

    assert "uv export --frozen --no-dev --no-emit-project" in workflow
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
    assert "windows-aggregate.json" in workflow
    assert "--junit-output" in workflow
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
    assert "gh release create \"${RELEASE_TAG}\" --verify-tag --draft" in workflow
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
