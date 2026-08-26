from __future__ import annotations

import re
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


def _job_block(workflow: str, name: str) -> str:
    matches = list(re.finditer(r"^  [a-z_]+:\n", workflow, re.MULTILINE))
    marker = f"  {name}:\n"
    for index, match in enumerate(matches):
        if match.group(0) != marker:
            continue
        end = matches[index + 1].start() if index + 1 < len(matches) else len(workflow)
        return workflow[match.start() : end]
    raise AssertionError(f"workflow job {name!r} is missing")


def test_external_has_six_role_domains_and_one_way_security_handoffs() -> None:
    workflow = _workflow("external-qualification-evidence.yml")
    parsed = yaml.safe_load(workflow)
    assert isinstance(parsed, dict)
    jobs = parsed.get("jobs")
    assert isinstance(jobs, dict)
    assert set(jobs) == {
        "candidate",
        "reference_freezer",
        "scorer_a",
        "scorer_b",
        "arbiter",
        "assembly",
    }
    labels = {
        "candidate": "deeplaw-qualification-candidate",
        "reference_freezer": "deeplaw-qualification-reference",
        "scorer_a": "deeplaw-qualification-scorer-a",
        "scorer_b": "deeplaw-qualification-scorer-b",
        "arbiter": "deeplaw-qualification-arbiter",
        "assembly": "deeplaw-qualification-assembly",
    }
    blocks = {name: _job_block(workflow, name) for name in labels}
    for name, label in labels.items():
        assert f"runs-on: [self-hosted, macOS, {label}]" in blocks[name]

    assert "actions/checkout@" not in blocks["reference_freezer"]
    assert '"${DEEPLAW_CODEX_CREDENTIAL_BROKER}"' in blocks["candidate"]
    assert '"${DEEPLAW_OPENCODE_CREDENTIAL_BROKER}"' in blocks["candidate"]
    assert "--dotenv \"${DEEPLAW_OPENCODE_DOTENV}\"" in blocks["candidate"]
    for name in ("reference_freezer", "scorer_a", "scorer_b", "arbiter", "assembly"):
        assert '"${DEEPLAW_CODEX_CREDENTIAL_BROKER}"' not in blocks[name]
        assert '"${DEEPLAW_OPENCODE_CREDENTIAL_BROKER}"' not in blocks[name]
        assert '"${DEEPLAW_OPENCODE_DOTENV}"' not in blocks[name]

    assert "candidate-sanitized-output" in blocks["scorer_a"]
    assert "sealed-reference" in blocks["scorer_a"]
    assert "path: scorer-inputs/scorer-b-output" not in blocks["scorer_a"]
    assert "path: scorer-inputs/arbiter-output" not in blocks["scorer_a"]
    assert "candidate-sanitized-output" in blocks["scorer_b"]
    assert "sealed-reference" in blocks["scorer_b"]
    assert "path: scorer-inputs/scorer-a-output" not in blocks["scorer_b"]
    assert "path: scorer-inputs/arbiter-output" not in blocks["scorer_b"]
    assert "scorer-a-output" in blocks["arbiter"]
    assert "scorer-b-output" in blocks["arbiter"]
    assert "path: arbiter-inputs/candidate-sanitized-output" not in blocks["arbiter"]
    assert "path: arbiter-inputs/sealed-reference" not in blocks["arbiter"]
    assert "DEEPLAW_SECURITY_DOMAIN_ATTESTER" in blocks["candidate"]
    for name in ("candidate", "reference_freezer", "scorer_a", "scorer_b", "arbiter"):
        assert "--attester-executable-sha256" in blocks[name]
        assert "--observed-root" in blocks[name]
        assert "--process-receipt" in blocks[name]

    assembly = blocks["assembly"]
    assert '"${DEEPLAW_SECURITY_DOMAIN_ATTESTER}"' not in assembly
    assert '"${DEEPLAW_CODEX_CREDENTIAL_BROKER}"' not in assembly
    assert '"${DEEPLAW_OPENCODE_CREDENTIAL_BROKER}"' not in assembly
    assert "--process-receipt" not in assembly
    assert "--exact-wheel-receipt" in assembly
    assert "candidate-qualification-inputs" in assembly


def test_candidate_full_freezes_construction_kernel_release_core_active_v3() -> None:
    workflow = _workflow("candidate-full.yml")

    assert "benchmarks/v013/active-qualification-v3.json" in workflow
    assert '--output "${RUNNER_TEMP}/verified-dist/frozen-active-qualification.json"' in workflow
    assert '--active-qualification "${verified}/frozen-active-qualification.json"' in workflow
    assert "active-qualification-v1.json" not in workflow
    assert "benchmarks.release.freeze_qualification_candidate_v3" in workflow
    assert "active-qualification-v2.json" not in workflow
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
    assert "windows-calibration-shards:" in workflow
    assert "windows-calibration-shard-${{ matrix.shard }}" in workflow
    assert "windows-calibration-aggregate.json" in workflow
    assert "windows-aggregate.json" in workflow
    assert "platform-matrix-receipt.json" in workflow
    assert "= 14" in workflow
    assert "retention-days: 90" in workflow


def test_candidate_full_runs_the_exact_10k_public_path_once() -> None:
    workflow = _workflow("candidate-full.yml")
    block = _job_block(workflow, "scale_ten_thousand")

    assert "needs: verified-artifact" in block
    assert "verified-candidate-artifacts" in block
    assert "benchmarks.v013.scale_qualification_v9" in block
    assert "--execute-10k" in block
    assert "--workflow-run-id \"${GITHUB_RUN_ID}\"" in block
    assert "--candidate-wheel-sha256" in block
    assert "--candidate-sdist-sha256" in block
    assert "scale-10000-evidence" in block
    assert "100k" not in block.casefold()

    aggregate = workflow.split("\n  aggregate-raw-evidence:\n", maxsplit=1)[1]
    assert "needs['scale_ten_thousand'].result == 'success'" in aggregate
    assert "v013-scale-qualification-v9.json" in aggregate


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

    assert "def check_path(" in workflow
    assert "current.lstat()" in workflow
    assert "path.is_symlink()" in workflow
    assert "stat.S_ISREG" in workflow
    assert "stat.S_ISDIR" in workflow
    assert "stat.S_ISLNK" in workflow
    assert "mode & 0o077" in workflow
    assert "owner_only=True" in workflow
    assert (
        'check_path("DEEPLAW_SECURITY_DOMAIN_ATTESTER", executable=True, owner_only=True)'
        in workflow
    )
    assert 'check_path("DEEPLAW_OPENCODE_DOTENV", owner_only=True)' in workflow
    assert 'check_path("DEEPLAW_QUALIFICATION_INPUTS", owner_only=True)' in workflow
    assert 'check_path("DEEPLAW_FINAL_BLIND_INPUTS", owner_only=True)' in workflow
    assert "source \"${DEEPLAW_OPENCODE_DOTENV}\"" not in workflow
    assert 'cat "${DEEPLAW_OPENCODE_DOTENV}"' not in workflow
    assert "auth.json" in workflow
    assert "transcript" in workflow
    assert "reasoning" in workflow
    assert "raw-events" in workflow


def test_external_requires_distinct_executable_scorer_a_b_and_arbiter_hashes() -> None:
    workflow = _workflow("external-qualification-evidence.yml")

    for name in (
        "DEEPLAW_SCORER_A",
        "DEEPLAW_SCORER_B",
        "DEEPLAW_DETERMINISTIC_ARBITER",
    ):
        assert f'test -f "${{{name}}}"' in workflow
        assert f'test ! -L "${{{name}}}"' in workflow
    assert "shasum -a 256 \"${DEEPLAW_SCORER_A}\"" in workflow
    assert "shasum -a 256 \"${DEEPLAW_SCORER_B}\"" in workflow
    assert "expected_scorer_sha256" in workflow
    assert "expected_arbiter_sha256" in workflow
    assert '["external_inputs"]["arbitration_sha256"]' in workflow
    assert workflow.count("frozen-active-qualification.json") >= 8
    assert "cmp -s" in workflow
    assert "--process-receipt scorer-a-output/result/process.json" in workflow
    assert "--process-receipt scorer-b-output/result/process.json" in workflow
    assert "--process-receipt arbiter-output/result/process.json" in workflow
    assert "--attester-executable-sha256" in workflow


def test_external_candidate_runner_cannot_receive_reference_scorer_or_dotenv_domains() -> None:
    workflow = _workflow("external-qualification-evidence.yml")
    candidate = workflow.split("  reference_freezer:", 1)[0]
    forbidden_tokens = {
        "--semantic-reference",
        "--agent-roster",
        "--agent-consensus",
        "--agent-isolation",
        "--scorer-a",
        "--scorer-b",
        "--deterministic-arbiter",
    }
    assert all(token not in candidate for token in forbidden_tokens)
    assert '"${DEEPLAW_REFERENCE_FREEZER}"' not in candidate
    assert '"${DEEPLAW_CODEX_CREDENTIAL_BROKER}" \\' in candidate
    assert '"${DEEPLAW_OPENCODE_CREDENTIAL_BROKER}" \\' in candidate
    assert "--candidate-inputs" in candidate
    assert "--verified-dist" in candidate
    assert "--provider-allowlist" in candidate
    assert "--network-policy host_provider_allowlist" in candidate
    assert "--process-receipt candidate-output/codex/process.json" in candidate
    assert "--process-receipt candidate-output/opencode/process.json" in candidate
    assert "--attester-executable-sha256" in candidate
    assert "--observed-root candidate-output" in candidate
    assert "DEEPLAW_OPENCODE_DOTENV" in candidate
    assert '--dotenv "${DEEPLAW_OPENCODE_DOTENV}"' in candidate
    assert "benchmarks.release.external_qualification_bundle_v4" in workflow
    assert "--qualification-run-id" not in workflow
    assert "python -m build" not in workflow
    assert "uv build" not in workflow
    assert "hatch build" not in workflow


def test_external_role_guards_are_deletion_sensitive() -> None:
    workflow = _workflow("external-qualification-evidence.yml")
    blocks = {
        name: _job_block(workflow, name)
        for name in (
            "candidate",
            "reference_freezer",
            "scorer_a",
            "scorer_b",
            "arbiter",
            "assembly",
        )
    }
    for token in (
        "DEEPLAW_REFERENCE_FREEZER",
        "DEEPLAW_REFERENCE_DOMAIN_RUNNER",
        "DEEPLAW_SCORER_A",
        "DEEPLAW_SCORER_B",
        "DEEPLAW_DETERMINISTIC_ARBITER",
    ):
        assert token in blocks["candidate"].split("test -n", 1)[0]
    for name in ("reference_freezer", "scorer_a", "scorer_b", "arbiter", "assembly"):
        prefix = blocks[name].split("test -n", 1)[0]
        assert "DEEPLAW_OPENCODE_DOTENV" in prefix
        assert "DEEPLAW_CODEX_CREDENTIAL_BROKER" in prefix
        assert "DEEPLAW_OPENCODE_CREDENTIAL_BROKER" in prefix
    assert "test ! -e scorer-inputs/scorer-b-output" in blocks["scorer_a"]
    assert "test ! -e scorer-inputs/arbiter-output" in blocks["scorer_a"]
    assert "test ! -e scorer-inputs/scorer-a-output" in blocks["scorer_b"]
    assert "test ! -e scorer-inputs/arbiter-output" in blocks["scorer_b"]
    assert "test ! -e arbiter-inputs/candidate-sanitized-output" in blocks["arbiter"]
    assert "test ! -e arbiter-inputs/sealed-reference" in blocks["arbiter"]
    assert "expected_arbiter_sha256" in blocks["arbiter"]
    assert "cmp -s" in blocks["arbiter"]


def test_external_keeps_exact_host_pins_and_delegates_auth_to_brokers() -> None:
    workflow = _workflow("external-qualification-evidence.yml")

    assert 'codex-cli 0.148.0-alpha.15' in workflow
    assert "7645c3caf5607e4528eb3a15b12496c284c2a918939aed34e863c760c1b421e7" in workflow
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


def test_repository_host_runners_accept_only_external_broker_launchers() -> None:
    opencode = (
        REPOSITORY / "benchmarks/hosts/run_pass13_opencode_continuity_qualification.py"
    ).read_text(encoding="utf-8")
    assert 'parser.add_argument("--opencode-launcher"' in opencode
    assert "--dotenv" not in opencode
    assert "load_deepseek_key" not in opencode
    assert '"runner_secret_received": False' in opencode
    assert '"runner_dotenv_path_received": False' in opencode

    workflow = _workflow("external-qualification-evidence.yml")
    broker_invocation = workflow.split(
        '          "${DEEPLAW_OPENCODE_CREDENTIAL_BROKER}" \\', 1
    )[1]
    broker_invocation = broker_invocation.split("          test -s", 1)[0]
    assert '--dotenv "${DEEPLAW_OPENCODE_DOTENV}"' in broker_invocation
    assert '--host-runner "${DEEPLAW_CANDIDATE_HOST_RUNNER}"' in broker_invocation


def test_external_uploads_only_sanitized_v4_bundle_without_gate_claims() -> None:
    workflow = _workflow("external-qualification-evidence.yml")

    assert "name: v013-qualification-evidence" in workflow
    assert "bundle-manifest.json" in workflow
    assert "retention-days: 90" in workflow
    assert "if-no-files-found: error" in workflow
    assert "include-hidden-files: false" in workflow
    assert "release_ready" not in workflow
    assert "claim_eligible" not in workflow


def test_kernel_evidence_executes_only_core_tasks_and_defers_bundle_run_binding() -> None:
    workflow = _workflow("kernel-qualification-evidence.yml")
    parsed = yaml.safe_load(workflow)
    trigger = parsed.get("on", parsed.get(True))

    assert set(trigger["workflow_dispatch"]["inputs"]) == {"candidate_run_id"}
    assert "runs-on: [self-hosted, macOS, deeplaw-kernel-qualification]" in workflow
    assert "DEEPLAW_KERNEL_EVIDENCE_COLLECTOR" in workflow
    assert "--transport-retry-limit 1" in workflow
    assert "DEEPLAW_HOST_IDENTITY_INPUT" in workflow
    frozen_identity = (
        'frozen_identity="${RUNNER_TEMP}/candidate-inputs/'
        'frozen-host-exact-identity.json"'
    )
    assert frozen_identity in workflow
    assert '--host-identity-input "${frozen_identity}"' in workflow
    handoff = workflow.split(
        "      - name: Build and validate the pre-execution Host task handoff", 1
    )[1].split(
        "      - name: Execute Codex x3, OpenCode x3, and deterministic Kernel evidence", 1
    )[0]
    assert "--build-handoff" in handoff
    assert "--validate-handoff \"${host_task_handoff}\"" in handoff
    assert '--host-identity-input "${frozen_identity}"' in handoff
    assert "host-task-handoff-validation.json" not in handoff
    collector = workflow.split(
        "      - name: Execute Codex x3, OpenCode x3, and deterministic Kernel evidence",
        1,
    )[1].split("      - name: Reopen every typed receipt with the repository validator", 1)[0]
    assert '--host-identity-input "${DEEPLAW_HOST_IDENTITY_INPUT}"' not in collector
    assert 'cp "${DEEPLAW_HOST_IDENTITY_INPUT}"' not in collector
    assert '--host-task-handoff "${host_task_handoff}"' in collector
    assert "-name '*host-task-handoff*'" in collector
    assert '-exec cmp -s "{}" "${host_task_handoff}"' in collector
    assert collector.index('--host-identity-input "${frozen_identity}"') < collector.index(
        '--host-task-handoff "${host_task_handoff}"'
    )
    reopen = workflow.split(
        "      - name: Reopen every typed receipt with the repository validator", 1
    )[1]
    assert frozen_identity in reopen
    assert '--host-identity-input "${frozen_identity}"' in reopen
    assert '--host-identity-input "${DEEPLAW_HOST_IDENTITY_INPUT}"' not in reopen
    assert 'cp "${DEEPLAW_HOST_IDENTITY_INPUT}"' not in reopen
    assert "codex-cli 0.148.0-alpha.15" not in workflow
    assert "7645c3caf5607e4528eb3a15b12496c284c2a918939aed34e863c760c1b421e7" not in workflow
    assert '"gpt-5.6-luna"' in workflow
    assert '"deepseek/deepseek-v4-flash"' in workflow
    assert "scale-10000-evidence/v013-scale-qualification-v9.json" in workflow
    assert "kernel_qualification_bundle_v1 build" in workflow
    assert "sentinel=9223372036854775807" in workflow
    assert 'rm "${root}/bundle-manifest.json"' in workflow
    assert "name: kernel-qualification-evidence" in workflow
    assert "retained-broker-source/codex.launcher-source" in workflow
    assert "retained-broker-source/opencode.launcher-source" in workflow
    assert 'source "${DEEPLAW_OPENCODE_DOTENV}"' not in workflow
    assert 'cat "${DEEPLAW_OPENCODE_DOTENV}"' not in workflow
    dotenv_section = workflow.split(
        'exact_file(\n              "DEEPLAW_OPENCODE_DOTENV"', maxsplit=1
    )[1].split("          identity_path", maxsplit=1)[0]
    assert "path.read_bytes()" not in dotenv_section
    for forbidden in (
        "deeplaw_reference_freezer",
        "deeplaw_scorer_a",
        "deeplaw_scorer_b",
        "deeplaw_deterministic_arbiter",
        "qualification_holdout",
        "final_blind",
        "superiority",
        "sota",
    ):
        assert forbidden not in workflow.casefold()


def test_commercial_qualification_dispatch_and_assembly_use_kernel_v9() -> None:
    workflow = _workflow("commercial-qualification.yml")
    trigger = _trigger(workflow)

    inputs = trigger["workflow_dispatch"]["inputs"]
    assert set(inputs) == {"candidate_run_id", "evidence_run_id"}
    assert "QUALIFICATION_RUN_ID: ${{ github.run_id }}" in workflow
    assert "--qualification-run-id \"${QUALIFICATION_RUN_ID}\"" in workflow
    assert "assemble_commercial_qualification_v9" in workflow
    assert "release_provenance_v9" in workflow
    assert "kernel_qualification_bundle_v1" in workflow
    assert "kernel-qualification-evidence.yml" in workflow
    assert "external_qualification_bundle_v4" not in workflow
    assert "assemble_commercial_qualification_v7" not in workflow
    assert "release_provenance_v7" not in workflow
    assert "v013-gate-classification-v8.json" not in workflow
    assert "qualification-protocol-v2.json" not in workflow
    assert "post_build_machine_reference_binding" not in workflow
    assert "candidate_machine_reference" not in workflow
    for forbidden in (
        "trusted-human-approver",
        "trusted-human",
        "semantic_gold",
        "Candidate Gold",
        "candidate_gold",
    ):
        assert forbidden not in workflow


def test_release_reopens_v9_kernel_provenance_and_keeps_public_state_separate() -> None:
    workflow = _workflow("release.yml")

    assert "release_provenance_v9" in workflow
    assert "kernel_qualification_bundle_v1" in workflow
    assert "kernel-qualification-evidence.yml" in workflow
    assert "external_qualification_bundle_v4" not in workflow
    assert "v013-gate-classification-v8.json" not in workflow
    assert "--candidate-machine-reference-binding" not in workflow
    assert "post_build_machine_reference_binding" not in workflow
    assert "candidate_machine_reference" not in workflow
    assert "commercial-release-assets" in workflow
    assert "release-bound-commercial-assets" in workflow
    assert "pre-publish-artifact-gate.json" in workflow
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
