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


def test_external_downloads_and_rebinds_both_candidate_full_artifacts() -> None:
    workflow = _workflow()
    assert workflow.count("actions/download-artifact@") == 2
    assert "name: verified-candidate-artifacts" in workflow
    assert "name: candidate-full-raw-evidence" in workflow
    assert "run-id: ${{ inputs.candidate_run_id }}" in workflow
    assert "candidate-full-run-receipt.json" in workflow
    assert "candidate-full-inventory-receipt.json" in workflow
    assert "deeplaw.retained-candidate-artifacts/v1" in workflow
    assert "git_commit" in workflow
    assert "git_tree" in workflow
    assert "lock_sha256" in workflow
    assert "retained wheel hash differs" in workflow
    assert "Candidate Full raw inventory does not match retained bytes" in workflow


def test_external_executes_only_the_unique_downloaded_wheel() -> None:
    workflow = _workflow()
    assert "benchmarks.release.exact_wheel_runner" in workflow
    assert "--candidate-dir" in workflow
    assert "--expected-wheel-sha256" in workflow
    assert "--requirements-filename candidate-requirements.txt" in workflow
    assert "--expected-requirements-sha256" in workflow
    assert "--expected-version" in workflow
    assert "--repository \"${GITHUB_WORKSPACE}\"" in workflow
    assert "external-exact-wheel-venv" in workflow
    assert "repository-external" in workflow
    assert "python -m build" not in workflow
    assert "uv build" not in workflow
    assert "hatch build" not in workflow


def test_host_preflight_uses_exact_pins_and_never_reads_auth_material() -> None:
    workflow = _workflow()
    assert "codex-cli 0.148.0-alpha.9" in workflow
    assert "6170ff5578170ee9b74ad92bfcff96e6186f41d02b60815a7c2b01ad424c754f" in workflow
    assert "gpt-5.6-luna" in workflow
    assert "--codex-reasoning-effort max" in workflow
    assert "login status" in workflow
    assert "1.18.16" in workflow
    assert "a3647eb025c7615159d417dcc49fc39fdaeba65b" in workflow
    assert "deepseek/deepseek-v4-flash" in workflow
    assert "deepseek-v4-flash" in workflow
    assert "DEEPLAW_CODEX_BINARY" in workflow
    assert "DEEPLAW_OPENCODE_BINARY" in workflow
    assert "DEEPLAW_OPENCODE_PACKAGE" in workflow
    assert "opencode_sha256" in workflow
    assert "opencode_package_sha256" in workflow
    assert "--opencode-sha256" in workflow
    assert "--opencode-package-sha256" in workflow
    assert "auth.json" in workflow  # negative path assertion is explicit in the workflow.
    assert "Keychain" not in workflow
    assert "~/.codex" not in workflow
    assert "codex exec" not in workflow
    assert "opencode run" not in workflow


def test_dotenv_and_external_inputs_are_metadata_only() -> None:
    workflow = _workflow()
    assert "DEEPLAW_OPENCODE_DOTENV" in workflow
    assert "--dotenv \"${DEEPLAW_OPENCODE_DOTENV}\"" in workflow
    assert "is_symlink()" in workflow
    assert "has_symlink_component" in workflow
    assert "owner_only" in workflow
    assert "mode & 0o077" in workflow
    assert "DEEPLAW_QUALIFICATION_ROOT" in workflow
    assert "DEEPLAW_HUMAN_GOLD_ROOT" in workflow
    assert "DEEPLAW_EXTERNAL_QUALIFICATION_RUNNER" in workflow
    assert "DEEPLAW_INDEPENDENT_SCORER" in workflow
    assert "DEEPLAW_TRUSTED_HUMAN_APPROVER" in workflow
    assert "source \"${DEEPLAW_OPENCODE_DOTENV}\"" not in workflow
    assert "cat \"${DEEPLAW_OPENCODE_DOTENV}\"" not in workflow


def test_runner_receipt_is_sanitized_and_v3_validator_is_the_only_bundle_boundary() -> None:
    workflow = _workflow()
    assert "--candidate-full-raw-root" in workflow
    assert "--verified-dist" in workflow
    assert "--exact-wheel-receipt" in workflow
    assert "raw_digests" in workflow
    assert "Candidate Full typed source is not retained Candidate Full evidence" in workflow
    assert "exact-wheel typed source differs from exact receipt" in workflow
    assert '"retained_supply_chain"' in workflow
    assert "--candidate-run-id \"${CANDIDATE_RUN_ID}\"" in workflow
    assert "--evidence-run-id \"${GITHUB_RUN_ID}\"" in workflow
    assert "--evidence-run-id \"${EVIDENCE_RUN_ID}\"" in workflow
    assert "--trusted-human-approver" in workflow
    assert "bundle-manifest.json" in workflow
    assert "test ! -e \"${bundle}/.env\"" in workflow
    assert "test ! -e \"${bundle}/auth.json\"" in workflow
    assert "test ! -e \"${bundle}/transcript\"" in workflow
    assert "test ! -e \"${bundle}/reasoning\"" in workflow
    assert "test ! -e \"${bundle}/raw-events\"" in workflow
    assert "benchmarks.release.external_qualification_bundle_v3" in workflow
    assert "--evidence-run-id \"${EVIDENCE_RUN_ID}\"" in workflow
    assert "retention-days: 90" in workflow
    assert "if-no-files-found: error" in workflow
    assert "include-hidden-files: false" in workflow
    assert "release_ready" not in workflow
    assert "claim_eligible" not in workflow


def test_current_typed_parser_is_loaded_without_making_a_gate_decision() -> None:
    workflow = _workflow()
    assert "benchmarks.release.typed_qualification_evidence" in workflow
    assert "parse_typed_evidence" in workflow
    assert "root=selected.parent" in workflow
    assert "root=bundle" not in workflow
    assert "benchmarks.release.external_qualification_bundle_v3" in workflow
    assert "passed" not in workflow.lower()
