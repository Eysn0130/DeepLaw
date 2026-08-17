from __future__ import annotations

from pathlib import Path

import yaml

REPOSITORY = Path(__file__).resolve().parents[1]


def _workflow(name: str) -> str:
    return (REPOSITORY / ".github" / "workflows" / name).read_text(encoding="utf-8")


def _trigger(workflow: str) -> dict[str, object]:
    parsed = yaml.safe_load(workflow)
    assert isinstance(parsed, dict)
    trigger = parsed.get("on", parsed.get(True))
    assert isinstance(trigger, dict)
    return trigger


def test_candidate_full_freezes_construction_machine_only_active_v2() -> None:
    workflow = _workflow("candidate-full.yml")

    assert "benchmarks/v013/active-qualification-v2.json" in workflow
    assert '--output "${RUNNER_TEMP}/verified-dist/frozen-active-qualification.json"' in workflow
    assert '--active-qualification "${verified}/frozen-active-qualification.json"' in workflow
    assert "active-qualification-v1.json" not in workflow
    assert "benchmarks.release.freeze_qualification_candidate_v2" in workflow
    assert "benchmarks.release.freeze_qualification_candidate " not in workflow


def test_candidate_full_retains_reproducible_artifact_and_raw_platform_evidence() -> None:
    workflow = _workflow("candidate-full.yml")

    assert "verify_reproducible_build" in workflow
    assert "retained_artifact_manifest" in workflow
    assert "verified-candidate-artifacts" in workflow
    assert "candidate-full-raw-evidence" in workflow
    assert "pre-publish-artifact-gate.json" in workflow
    assert "deeplaw.cdx.json" in workflow
    assert "openvex.json" in workflow
    assert "installed-licenses.json" in workflow
    assert "candidate-tests.xml" in workflow
    assert "windows-calibration.xml" in workflow
    assert "windows-aggregate.json" in workflow
    assert "platform-matrix-receipt.json" in workflow
    assert "= 14" in workflow
    assert "retention-days: 90" in workflow


def test_external_dispatch_requires_only_candidate_run_id() -> None:
    workflow = _workflow("external-qualification-evidence.yml")
    trigger = _trigger(workflow)

    assert set(trigger) == {"workflow_dispatch"}
    inputs = trigger["workflow_dispatch"]["inputs"]
    assert set(inputs) == {"candidate_run_id"}
    assert inputs["candidate_run_id"]["required"] is True
    assert "CANDIDATE_RUN_ID" in workflow
    assert "QUALIFICATION_RUN_ID" not in workflow
    assert 'test "${CANDIDATE_RUN_ID}" != "${GITHUB_RUN_ID}"' in workflow


def test_external_interface_is_machine_only_and_has_no_legacy_human_or_singular_scorer() -> None:
    workflow = _workflow("external-qualification-evidence.yml")

    for forbidden in (
        "DEEPLAW_HUMAN_GOLD_ROOT",
        "DEEPLAW_TRUSTED_HUMAN_APPROVER",
        "DEEPLAW_INDEPENDENT_SCORER",
        "--human-gold-root",
        "--trusted-human-approver",
        "--independent-scorer",
        "--scorer ",
        "external_qualification_bundle_v3",
        "typed_qualification_evidence",
    ):
        assert forbidden not in workflow
    for required in (
        "DEEPLAW_REFERENCE_FREEZER",
        "DEEPLAW_REFERENCE_CASES",
        "DEEPLAW_REVIEWER_OUTPUT_1",
        "DEEPLAW_REVIEWER_OUTPUT_2",
        "DEEPLAW_REVIEWER_OUTPUT_3",
        "DEEPLAW_CANDIDATE_HOST_RUNNER",
        "DEEPLAW_CODEX_CREDENTIAL_BROKER",
        "DEEPLAW_OPENCODE_CREDENTIAL_BROKER",
        "DEEPLAW_EVIDENCE_ASSEMBLER",
        "DEEPLAW_SCORER_A",
        "DEEPLAW_SCORER_B",
        "DEEPLAW_DETERMINISTIC_ARBITER",
        "machine-only v4",
    ):
        assert required in workflow


def test_external_inputs_are_owner_controlled_regular_paths_with_closed_env() -> None:
    workflow = _workflow("external-qualification-evidence.yml")

    assert "has_symlink_component" in workflow
    assert "is_symlink()" in workflow
    assert "os.lstat" in workflow
    assert "stat.S_ISREG" in workflow
    assert "outside the checkout" in workflow
    assert "must be outside the checkout" in workflow
    assert "mode & 0o077" in workflow
    assert "owner_only=True" in workflow
    assert "source \"${DEEPLAW_OPENCODE_DOTENV}\"" not in workflow
    assert 'cat "${DEEPLAW_OPENCODE_DOTENV}"' not in workflow
    assert "auth.json" in workflow
    assert "transcript" in workflow
    assert "reasoning" in workflow
    assert "raw-events" in workflow


def test_external_requires_distinct_executable_scorer_a_b_and_arbiter_hashes() -> None:
    workflow = _workflow("external-qualification-evidence.yml")

    assert 'regular_file("DEEPLAW_SCORER_A", executable=True)' in workflow
    assert 'regular_file("DEEPLAW_SCORER_B", executable=True)' in workflow
    assert 'regular_file("DEEPLAW_DETERMINISTIC_ARBITER", executable=True)' in workflow
    assert "scorer_a_sha256" in workflow
    assert "scorer_b_sha256" in workflow
    assert "arbiter_sha256" in workflow
    assert 'test "${scorer_a_sha256}" != "${scorer_b_sha256}"' in workflow
    assert 'test "${scorer_a_sha256}" != "${arbiter_sha256}"' in workflow
    assert 'test "${scorer_b_sha256}" != "${arbiter_sha256}"' in workflow
    assert '"${DEEPLAW_SCORER_A}" \\' in workflow
    assert '"${DEEPLAW_SCORER_B}" \\' in workflow
    assert '"${DEEPLAW_DETERMINISTIC_ARBITER}" \\' in workflow
    assert '--process-receipt "${scorer_a}/process.json"' in workflow
    assert '--process-receipt "${scorer_b}/process.json"' in workflow
    assert '--process-receipt "${arbitration}/process.json"' in workflow
    assert "exact_wheel_receipt_sha256" in workflow
    assert "external runner replaced the exact-wheel execution receipt" in workflow


def test_external_candidate_runner_cannot_receive_reference_scorer_or_dotenv_domains() -> None:
    workflow = _workflow("external-qualification-evidence.yml")
    forbidden_tokens = {
        "--semantic-reference",
        "--agent-roster",
        "--agent-consensus",
        "--agent-isolation",
        "--scorer-a",
        "--scorer-b",
        "--deterministic-arbiter",
        "--dotenv",
        "DEEPLAW_OPENCODE_DOTENV",
    }
    before_mode, after_mode = workflow.split("--mode non-host", 1)
    candidate_invocation = before_mode.rsplit("          env -i", 1)[1]
    candidate_invocation += "--mode non-host"
    candidate_invocation += after_mode.split("          env -i", 1)[0]
    assert "--candidate-full-raw-root" in candidate_invocation
    assert "--verified-dist" in candidate_invocation
    assert all(token not in candidate_invocation for token in forbidden_tokens)
    assert '"${DEEPLAW_REFERENCE_FREEZER}" \\' in workflow
    assert '--candidate-visible false' in workflow
    assert '"${DEEPLAW_CODEX_CREDENTIAL_BROKER}" \\' in workflow
    assert '"${DEEPLAW_OPENCODE_CREDENTIAL_BROKER}" \\' in workflow
    assert "benchmarks.release.external_qualification_bundle_v4" in workflow
    assert "--qualification-run-id" not in workflow
    assert "python -m build" not in workflow
    assert "uv build" not in workflow
    assert "hatch build" not in workflow


def test_external_keeps_exact_host_pins_and_delegates_auth_to_brokers() -> None:
    workflow = _workflow("external-qualification-evidence.yml")

    assert 'codex-cli 0.148.0-alpha.9' in workflow
    assert "6170ff5578170ee9b74ad92bfcff96e6186f41d02b60815a7c2b01ad424c754f" in workflow
    assert "gpt-5.6-luna" in workflow
    assert 'login status' not in workflow
    assert "DEEPLAW_CODEX_CREDENTIAL_BROKER" in workflow
    assert "DEEPLAW_OPENCODE_CREDENTIAL_BROKER" in workflow
    assert '1.18.16' in workflow
    assert 'a3647eb025c7615159d417dcc49fc39fdaeba65b' in workflow
    assert 'deepseek/deepseek-v4-flash' in workflow
    assert 'deepseek-v4-flash' in workflow
    assert "~/.codex" not in workflow
    assert "Keychain" not in workflow
    assert "codex exec" not in workflow
    assert "opencode run" not in workflow


def test_external_uploads_only_sanitized_v4_bundle_without_gate_claims() -> None:
    workflow = _workflow("external-qualification-evidence.yml")

    assert "name: v013-qualification-evidence" in workflow
    assert "bundle-manifest.json" in workflow
    assert "retention-days: 90" in workflow
    assert "if-no-files-found: error" in workflow
    assert "include-hidden-files: false" in workflow
    assert "release_ready" not in workflow
    assert "claim_eligible" not in workflow


def test_commercial_qualification_dispatch_and_assembly_are_machine_only_v8() -> None:
    workflow = _workflow("commercial-qualification.yml")
    trigger = _trigger(workflow)

    inputs = trigger["workflow_dispatch"]["inputs"]
    assert set(inputs) == {"candidate_run_id", "evidence_run_id"}
    assert "QUALIFICATION_RUN_ID: ${{ github.run_id }}" in workflow
    assert "--qualification-run-id \"${GITHUB_RUN_ID}\"" in workflow
    assert "assemble_commercial_qualification_v8" in workflow
    assert "release_provenance_v8" in workflow
    assert "external_qualification_bundle_v4" not in workflow
    assert "assemble_commercial_qualification_v7" not in workflow
    assert "release_provenance_v7" not in workflow
    assert "v013-gate-classification-v8.json" in workflow
    assert "qualification-protocol-v2.json" in workflow
    assert "--semantic-reference-sha256" in workflow
    assert "--candidate-binding-sha256" in workflow
    assert "--final-blind-holdout-sha256" in workflow
    assert "--scorer-panel-sha256" in workflow
    assert "--compiler-scorer-isolation-sha256" in workflow
    assert "post_build_machine_reference_binding" in workflow
    assert "candidate_machine_reference" in workflow
    for forbidden in (
        "trusted-human-approver",
        "trusted-human",
        "semantic_gold",
        "Candidate Gold",
        "candidate_gold",
    ):
        assert forbidden not in workflow


def test_release_reopens_v8_transitive_provenance_and_keeps_public_state_separate() -> None:
    workflow = _workflow("release.yml")

    assert "release_provenance_v8" in workflow
    assert "external_qualification_bundle_v4" in workflow
    assert "v013-gate-classification-v8.json" in workflow
    assert "--candidate-machine-reference-binding" in workflow
    assert "post_build_machine_reference_binding" in workflow
    assert "candidate_machine_reference" in workflow
    assert "commercial-release-assets" in workflow
    assert "pre_publish_artifact_gate" in workflow
    assert "public_release_verified" in workflow
    assert "tree: ${{ steps.release.outputs.tree }}" in workflow
    assert "tree=$(git rev-parse HEAD^{tree})" in workflow
    assert "RELEASE_TREE: ${{ needs.resolve.outputs.tree }}" in workflow
    assert '"schema_version": "deeplaw.post-public-verification/v2"' in workflow
    assert "post-public-verification.v2.schema.json" in workflow
    assert '"commercial_manifest_sha256"' in workflow
    assert '"sha256s_sha256"' in workflow
    assert "owner_release_confirmation" in workflow
    assert 'test "${GITHUB_ACTOR}" = "${GITHUB_REPOSITORY_OWNER}"' in workflow
    assert 'test "${OWNER_RELEASE_CONFIRMATION}" = "publish-v0.13.0"' in workflow
    assert "verify_run()" in workflow
    assert '--jq .status' in workflow
    assert '--jq .name' in workflow
    assert '--jq .path' in workflow
    for forbidden in (
        "release_provenance_v7",
        "external_qualification_bundle_v3",
        "trusted-human-approver",
        "candidate-gold-binding",
        "candidate_gold",
        "gold binding",
    ):
        assert forbidden not in workflow


def test_release_resume_rejects_remote_extra_assets_without_deleting_them() -> None:
    workflow = _workflow("release.yml")
    resume = workflow.split(
        "      - name: Create or resume the draft or public prerelease without overwriting assets",
        maxsplit=1,
    )[1].split("      - name: Publish the verified draft as a public prerelease", maxsplit=1)[0]

    assert "allowed_assets=$(mktemp)" in resume
    assert "gh api --paginate" in resume
    assert "comm -23" in resume
    assert "remote release contains assets outside the local publish allowlist" in resume
    assert "post-public-verification.json" in resume
    assert "gh release delete-asset" not in resume

    pre_public = workflow.split("\n  publish:", maxsplit=1)[0]
    assert "post-public-verification.v2.schema.json" not in pre_public
