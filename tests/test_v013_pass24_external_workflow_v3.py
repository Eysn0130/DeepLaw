from __future__ import annotations

from pathlib import Path

import yaml

REPOSITORY = Path(__file__).resolve().parents[1]
WORKFLOW = REPOSITORY / ".github/workflows/external-qualification-evidence.yml"


def _workflow() -> str:
    return WORKFLOW.read_text(encoding="utf-8")


def _parsed() -> dict[str, object]:
    value = yaml.safe_load(_workflow())
    assert isinstance(value, dict)
    return value


def test_external_dispatch_requires_one_candidate_full_run_id() -> None:
    parsed = _parsed()
    trigger = parsed.get("on", parsed.get(True))
    assert isinstance(trigger, dict)
    assert set(trigger) == {"workflow_dispatch"}
    inputs = trigger["workflow_dispatch"]["inputs"]
    assert set(inputs) == {"candidate_run_id"}
    assert inputs["candidate_run_id"]["required"] is True
    workflow = _workflow()
    assert "Candidate Full" in workflow
    assert "--jq .head_sha" in workflow
    assert "--jq .path" in workflow
    assert ".github/workflows/candidate-full.yml" in workflow


def test_candidate_runner_temp_paths_are_scoped_to_steps() -> None:
    candidate = _parsed()["jobs"]["candidate"]
    assert "env" not in candidate
    expected = {
        "CANDIDATE_INPUT_ROOT": "${{ runner.temp }}/candidate-inputs",
        "DIST_ROOT": "${{ runner.temp }}/candidate-dist",
        "EXACT_WHEEL_RECEIPT": "${{ runner.temp }}/exact-wheel-receipt.json",
        "EXACT_WHEEL_VENV": "${{ runner.temp }}/exact-wheel-venv",
    }
    observed = {name: 0 for name in expected}
    for step in candidate["steps"]:
        run = step.get("run", "")
        env = step.get("env", {})
        for name, value in expected.items():
            if name in run:
                assert env.get(name) == value
                observed[name] += 1
    assert observed == {
        "CANDIDATE_INPUT_ROOT": 5,
        "DIST_ROOT": 2,
        "EXACT_WHEEL_RECEIPT": 4,
        "EXACT_WHEEL_VENV": 2,
    }


def test_external_downloads_and_rebinds_both_candidate_full_artifacts() -> None:
    parsed = _parsed()
    jobs = parsed["jobs"]
    assert set(jobs) == {
        "candidate",
        "reference_freezer",
        "scorer_a",
        "scorer_b",
        "arbiter",
        "assembly",
    }
    assert jobs["scorer_a"]["needs"] == ["candidate", "reference_freezer"]
    assert jobs["scorer_b"]["needs"] == ["candidate", "reference_freezer"]
    assert jobs["arbiter"]["needs"] == ["scorer_a", "scorer_b"]
    assert jobs["assembly"]["needs"] == [
        "candidate",
        "reference_freezer",
        "scorer_a",
        "scorer_b",
        "arbiter",
    ]
    workflow = _workflow()
    assert workflow.count("actions/download-artifact@") >= 8
    assert "name: verified-candidate-artifacts" in workflow
    assert "name: candidate-full-raw-evidence" in workflow
    assert "run-id: ${{ inputs.candidate_run_id }}" in workflow
    assert "candidate-full-run-receipt.json" in workflow
    assert "candidate-full-inventory-receipt.json" in workflow
    assert "benchmarks.release.retained_artifact_manifest" in workflow
    assert "--verify" in workflow
    assert "shasum -a 256" in workflow
    assert "candidate_full_raw_inventory" in workflow
    assert "declared" in workflow
    assert "observed" in workflow
    assert "Candidate Full raw inventory does not match retained bytes" in workflow
    candidate_block = workflow[
        workflow.index("  candidate:") : workflow.index("  reference_freezer:")
    ]
    assert "grep -F" not in candidate_block


def test_external_executes_only_the_unique_downloaded_wheel() -> None:
    workflow = _workflow()
    assert "benchmarks.release.exact_wheel_runner" in workflow
    assert "--candidate-dir" in workflow
    assert "--expected-wheel-sha256" in workflow
    assert "--requirements-filename candidate-requirements.txt" in workflow
    assert "--expected-requirements-sha256" in workflow
    assert "--expected-version" in workflow
    assert "--repository \"${GITHUB_WORKSPACE}\"" in workflow
    assert "exact-wheel-venv" in workflow
    assert "repository-external" in workflow
    assert "python -m build" not in workflow
    assert "uv build" not in workflow
    assert "hatch build" not in workflow


def test_host_preflight_uses_exact_pins_and_never_reads_auth_material() -> None:
    workflow = _workflow()
    candidate = workflow[
        workflow.index("  candidate:") : workflow.index("  reference_freezer:")
    ]
    assert candidate.count("\n          import hashlib\n") == 2
    assert candidate.count("\n          import json\n") == 2
    assert "codex-cli 0.148.0-alpha.15" in workflow
    assert "7645c3caf5607e4528eb3a15b12496c284c2a918939aed34e863c760c1b421e7" in workflow
    assert "gpt-5.6-luna" in workflow
    assert "--reasoning-effort max" in workflow
    assert "login status" not in workflow
    assert "DEEPLAW_CODEX_CREDENTIAL_BROKER" in workflow
    assert "1.18.16" in workflow
    assert "a3647eb025c7615159d417dcc49fc39fdaeba65b" in workflow
    assert "deepseek/deepseek-v4-flash" in workflow
    assert "deepseek-v4-flash" in workflow
    assert "DEEPLAW_CODEX_BINARY" in workflow
    assert "DEEPLAW_OPENCODE_BINARY" in workflow
    assert "DEEPLAW_OPENCODE_PACKAGE" in workflow
    assert "opencode_sha256" in workflow
    assert "opencode_package_sha256" in workflow
    assert "DIST_ROOT" in workflow
    assert "EXACT_WHEEL_RECEIPT" in workflow
    assert "EXACT_WHEEL_VENV" in workflow
    assert "owner_only" in workflow
    assert "stat.S_IMODE" in workflow
    assert "S_ISLNK" in workflow
    assert "DEEPLAW_HOST_PROVIDER_ALLOWLIST" in workflow
    assert "--network-policy host_provider_allowlist" in workflow
    assert "--provider-allowlist" in workflow
    assert "DEEPLAW_SECURITY_DOMAIN_ATTESTER_SHA256" in workflow
    assert "--opencode-sha256" in workflow
    assert "--opencode-package-sha256" in workflow
    assert "auth.json" in workflow  # negative path assertion is explicit in the workflow.
    assert "Keychain" not in workflow
    assert "~/.codex" not in workflow
    assert "codex exec" not in workflow
    assert "opencode run" not in workflow


def test_dotenv_and_external_inputs_are_metadata_only() -> None:
    workflow = _workflow()
    assert "! find " not in workflow
    assert "! grep " not in workflow
    assert workflow.count("forbidden_path=\"$(find ") == 4
    assert all(
        "-print -quit" in line
        for line in workflow.splitlines()
        if 'forbidden_path="$(find ' in line
    )
    assert workflow.count('test -z "${forbidden_path}"') == 4
    assert 'if grep -Fq -- "$1" "$2"; then' in workflow
    assert 'test "${grep_status}" -eq 1' in workflow
    assert "DEEPLAW_OPENCODE_DOTENV" in workflow
    assert "--dotenv \"${DEEPLAW_OPENCODE_DOTENV}\"" in workflow
    assert "DEEPLAW_SECURITY_DOMAIN_ATTESTER" in workflow
    assert "deeplaw-qualification-reference" in workflow
    assert "deeplaw-qualification-candidate" in workflow
    assert "deeplaw-qualification-scorer-a" in workflow
    assert "deeplaw-qualification-scorer-b" in workflow
    assert "deeplaw-qualification-arbiter" in workflow
    assert "deeplaw-qualification-assembly" in workflow
    assert "DEEPLAW_REFERENCE_FREEZER" in workflow
    assert "DEEPLAW_REFERENCE_CASES" in workflow
    assert "DEEPLAW_REVIEWER_OUTPUT_1" in workflow
    assert "DEEPLAW_REVIEWER_PROCESS_3" in workflow
    assert "DEEPLAW_CANDIDATE_HOST_RUNNER" in workflow
    assert "DEEPLAW_EXTERNAL_QUALIFICATION_RUNNER" not in workflow
    assert "DEEPLAW_SCORER_A" in workflow
    assert "DEEPLAW_SCORER_B" in workflow
    assert "DEEPLAW_DETERMINISTIC_ARBITER" in workflow
    assert "DEEPLAW_HUMAN_GOLD_ROOT" not in workflow
    assert "DEEPLAW_TRUSTED_HUMAN_APPROVER" not in workflow
    assert "source \"${DEEPLAW_OPENCODE_DOTENV}\"" not in workflow
    assert "cat \"${DEEPLAW_OPENCODE_DOTENV}\"" not in workflow


def test_security_domains_use_five_role_handoff_and_attest_after_process() -> None:
    workflow = _workflow()
    assert "--role reference_freezer" in workflow
    assert "--role candidate_host" in workflow
    assert "--role scorer_a" in workflow
    assert "--role scorer_b" in workflow
    assert "--role arbiter" in workflow
    assert "--role assembly" not in workflow
    assert "security/assembly" not in workflow
    assert "--process-receipt" in workflow
    assert (
        "--process-receipt candidate-output/codex/process.json" in workflow
        and "--process-receipt candidate-output/opencode/process.json" in workflow
    )
    assert "--observed-root" in workflow
    assert "--attester-executable-sha256" in workflow

    def section(role: str, next_role: str) -> str:
        start = workflow.index(f"  {role}:")
        end = workflow.index(f"  {next_role}:", start)
        return workflow[start:end]

    candidate = section("candidate", "reference_freezer")
    assert candidate.index("DEEPLAW_CODEX_CREDENTIAL_BROKER") < candidate.rindex(
        "DEEPLAW_SECURITY_DOMAIN_ATTESTER"
    )
    reference = section("reference_freezer", "scorer_a")
    assert reference.index("DEEPLAW_REFERENCE_DOMAIN_RUNNER") < reference.rindex(
        "DEEPLAW_SECURITY_DOMAIN_ATTESTER"
    )
    scorer_a = section("scorer_a", "scorer_b")
    assert scorer_a.index("DEEPLAW_SCORER_A") < scorer_a.rindex(
        "DEEPLAW_SECURITY_DOMAIN_ATTESTER"
    )
    scorer_b = section("scorer_b", "arbiter")
    assert scorer_b.index("DEEPLAW_SCORER_B") < scorer_b.rindex(
        "DEEPLAW_SECURITY_DOMAIN_ATTESTER"
    )
    arbiter = section("arbiter", "assembly")
    assert arbiter.index("DEEPLAW_DETERMINISTIC_ARBITER") < arbiter.rindex(
        "DEEPLAW_SECURITY_DOMAIN_ATTESTER"
    )
    assembly = workflow[workflow.index("  assembly:") :]
    assert "--role assembly" not in assembly
    assert "name: candidate-qualification-inputs" in assembly
    assert "--candidate-full-raw-root" in assembly
    assert "--verified-dist" in assembly
    assert "--exact-wheel-receipt" in assembly
    assert "--active-qualification" in assembly
    assert "--security-domain-root" in assembly


def test_candidate_external_files_match_the_frozen_active_hashes() -> None:
    workflow = _workflow()
    assert 'check_path("DEEPLAW_QUALIFICATION_INPUTS", owner_only=True)' in workflow
    assert 'check_path("DEEPLAW_FINAL_BLIND_INPUTS", owner_only=True)' in workflow
    assert 'check_path("DEEPLAW_QUALIFICATION_INPUTS", directory=True' not in workflow
    assert '"qualification_holdout_sha256"' in workflow
    assert '"final_blind_holdout_sha256"' in workflow
    assert '"isolation_sha256"' in workflow
    assert '"executable_sha256"' in workflow
    assert '"package_sha256"' in workflow
    assert "external input differs from frozen active qualification" in workflow


def test_forbidden_artifacts_are_not_downloaded_by_reference_or_scoring_domains() -> None:
    workflow = _workflow()

    def section(role: str, next_role: str) -> str:
        start = workflow.index(f"  {role}:")
        end = workflow.index(f"  {next_role}:", start)
        return workflow[start:end]

    reference = section("reference_freezer", "scorer_a")
    assert "actions/checkout@" not in reference
    assert "actions/download-artifact@" not in reference
    scorer_a = section("scorer_a", "scorer_b")
    scorer_b = section("scorer_b", "arbiter")
    for scorer in (scorer_a, scorer_b):
        assert "name: candidate-sanitized-output" in scorer
        assert "name: sealed-reference" in scorer
        assert "candidate-qualification-inputs" not in scorer
        assert "name: arbiter-output" not in scorer


def test_runner_receipt_is_sanitized_and_v4_validator_is_the_only_bundle_boundary() -> None:
    workflow = _workflow()
    assert "--candidate-output-root" in workflow
    assert "--verified-dist" in workflow
    assert "exact-wheel-receipt" in workflow
    assert "--candidate-run-id" in workflow
    assert '--evidence-run-id "${EVIDENCE_RUN_ID}"' in workflow
    assert "--qualification-run-id" not in workflow
    assert "--trusted-human-approver" not in workflow
    assert "bundle-manifest.json" in workflow
    assert "test ! -e \"${bundle}/.env\"" in workflow
    assert "test ! -e \"${bundle}/auth.json\"" in workflow
    assert "test ! -e \"${bundle}/transcript\"" in workflow
    assert "test ! -e \"${bundle}/reasoning\"" in workflow
    assert "test ! -e \"${bundle}/raw-events\"" in workflow
    assert "benchmarks.release.external_qualification_bundle_v4" in workflow
    assert "machine-only v4" in workflow
    assert "retention-days: 90" in workflow
    assert "if-no-files-found: error" in workflow
    assert "include-hidden-files: false" in workflow
    assert "release_ready" not in workflow
    assert "claim_eligible" not in workflow


def test_current_v4_bundle_validator_is_loaded_without_making_a_gate_decision() -> None:
    workflow = _workflow()
    assert "benchmarks.release.external_qualification_bundle_v4" in workflow
    assert '--root "${bundle}"' in workflow
    assert "--active-qualification \"${active}\"" in workflow
    assert "--candidate-run-id \"${CANDIDATE_RUN_ID}\"" in workflow
    assert "--evidence-run-id \"${EVIDENCE_RUN_ID}\"" in workflow
    assert "benchmarks.release.external_qualification_bundle_v3" not in workflow
    assert "benchmarks.release.typed_qualification_evidence" not in workflow
    assert "DEEPLAW_HUMAN_GOLD_ROOT" not in workflow
    assert "DEEPLAW_TRUSTED_HUMAN_APPROVER" not in workflow
