"""Freeze a complete Platform Core manifest from pytest collection.

This maintainer-only command never changes test selection. It records the exact
node IDs produced by the repository's closed marker expressions and preserves
known JUnit identities from the historical v1 manifest.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

from benchmarks.release.evidence import canonical_json, sha256_bytes
from benchmarks.release.platform_inventory import collect_node_ids

REPOSITORY = Path(__file__).resolve().parents[2]
HISTORICAL_MANIFEST = (
    REPOSITORY / "benchmarks/release/platform-core-test-manifest-v1.json"
)
HISTORICAL_COMPATIBILITY_NODE_ID = (
    "tests/test_identity_migration_v060.py::"
    "test_real_v060_wheel_additive_migration_verification_and_rollback"
)
POSIX_ONLY_ON_WINDOWS_NODE_IDS: tuple[str, ...] = (
    "tests/test_v013_owner_external_collector.py::"
    "test_frozen_collector_survives_ambient_path_replacement",
    "tests/test_v013_owner_external_collector.py::"
    "test_source_must_be_owner_only_and_credential_free",
    "tests/test_v013_owner_external_collector.py::"
    "test_tampered_frozen_collector_fails_closed",
    "tests/test_v013_owner_external_collector.py::"
    "test_wrong_run_binding_and_identity_tamper_fail_closed",
    "tests/test_v013_host_process_receipt_v2.py::"
    "test_codex_posix_close_cleans_group_after_leader_exit",
    "tests/test_v013_host_process_receipt_v2.py::"
    "test_codex_posix_group_cleanup_send_failure_is_unconfirmed",
    "tests/test_v013_host_process_receipt_v2.py::"
    "test_codex_posix_start_cleans_stale_group_after_leader_exit",
    "tests/test_v013_host_process_receipt_v2.py::"
    "test_codex_posix_start_uses_new_session_process_group",
    "tests/test_v013_pass13_opencode_qualification.py::"
    "test_owner_broker_process_group_cleanup_send_failure_is_unconfirmed",
    "tests/test_v013_pass13_opencode_qualification.py::"
    "test_owner_broker_success_fails_closed_when_final_cleanup_is_unconfirmed",
    "tests/test_v013_pass13_opencode_qualification.py::"
    "test_posix_process_tree_cleanup_kills_group_after_leader_exit",
    "tests/test_v013_pass13_opencode_qualification.py::"
    "test_posix_process_tree_cleanup_send_failure_is_unconfirmed",
)


NATIVE_NONAPPLICABLE_TESTS: dict[str, tuple[str, ...]] = {
    ("tests/test_macos_virtual_slot_source.py::"
     "test_validation_only_accepts_explicit_bounded_config_without_starting_vm"):
        ("Linux", "Windows"),
    ("tests/test_macos_virtual_slot_source.py::"
     "test_validation_only_rejects_unknown_cli_before_vm_start"): ("Linux", "Windows"),
    ("tests/test_macos_virtual_slot_source.py::"
     "test_validation_only_rejects_missing_required_cli_before_vm_start"): ("Linux", "Windows"),
    ("tests/test_macos_virtual_slot_source.py::"
     "test_validation_only_rejects_budget_out_of_bounds"): ("Linux", "Windows"),
    ("tests/test_macos_virtual_slot_source.py::"
     "test_validation_only_rejects_missing_image_before_vm_start"): ("Linux", "Windows"),
    ("tests/test_macos_virtual_slot_source.py::"
     "test_validation_only_requires_fixed_port_and_owner_local_handoff_pair"): ("Linux", "Windows"),
    ("tests/test_macos_virtual_slot_source.py::"
     "test_validation_only_rejects_unbounded_or_public_handoff_socket_path"): ("Linux", "Windows"),
    ("tests/test_endpoint_security_observer.py::"
     "test_native_probe_reports_actual_access_without_subscribing"): ("Linux", "Windows"),
    ("tests/test_linux_mcp_socket_transport.py::"
     "test_mcp_endpoint_uses_actual_peer_uid_and_removes_socket_path"): ("Darwin", "Windows"),
    ("tests/test_linux_mcp_socket_transport.py::"
     "test_mcp_endpoint_rejects_actual_peer_uid_mismatch"): ("Darwin", "Windows"),
    ("tests/test_linux_guest_slot_control.py::"
     "test_original_fork_sources_stay_private_and_are_hash_bound"): ("Windows",),
    ("tests/test_linux_guest_slot_control.py::"
     "test_http_child_timeout_is_reaped_and_reported"): ("Windows",),
    ("tests/test_linux_guest_slot_control.py::"
     "test_real_child_result_preserves_success_fields"): ("Windows",),
    ("tests/test_native_fork_observation.py::"
     "test_snapshot_plugin_log_is_frozen_and_missing_is_empty"): ("Windows",),
    ("tests/test_native_fork_observation.py::"
     "test_snapshot_rejects_symlink_ancestors_and_nonregular_files"): ("Windows",),
    ("tests/test_native_fork_observation.py::"
     "test_await_child_event_uses_new_complete_line_and_original_hash"): ("Windows",),
    ("tests/test_native_fork_observation.py::"
     "test_await_child_event_owner_callback_receives_record_without_newline"): ("Windows",),
    ("tests/test_native_fork_observation.py::"
     "test_await_child_event_owner_callback_failure_is_a_typed_gap"): ("Windows",),
    ("tests/test_native_fork_observation.py::"
     "test_await_child_event_owner_callback_cannot_outlive_deadline"): ("Windows",),
    ("tests/test_native_fork_observation.py::"
     "test_await_child_event_rejects_settled_event_after_deadline"): ("Windows",),
    ("tests/test_native_fork_observation.py::"
     "test_await_child_event_does_not_reuse_old_child_event"): ("Windows",),
    ("tests/test_native_fork_observation.py::"
     "test_await_child_event_rejects_malformed_or_unknown_rows"): ("Windows",),
    ("tests/test_native_fork_observation.py::"
     "test_await_child_event_rejects_duplicate_child_and_prefix_tampering"): ("Windows",),
    ("tests/test_native_fork_observation.py::"
     "test_await_child_event_rejects_snapshot_tamper_and_unbounded_timeout"): ("Windows",),
    ("tests/test_native_fork_observation.py::"
     "test_await_child_event_rejects_directory_replacement_and_oversize"): ("Windows",),
    ("tests/test_native_preflight_initrd.py::"
     "test_production_registry_stages_current_repo_assets"): ("Windows",),
    ("tests/test_native_preflight_initrd.py::"
     "test_fork_route_purpose_is_bound_into_guest_and_build_receipt"): ("Windows",),
    ("tests/test_native_preflight_initrd.py::"
     "test_build_is_reproducible_and_archive_is_path_free"): ("Windows",),
    ("tests/test_native_preflight_initrd.py::"
     "test_manifest_digest_and_closed_keys_fail_closed"): ("Windows",),
    ("tests/test_native_preflight_initrd.py::"
     "test_symlink_input_is_rejected_without_creating_destination"): ("Windows",),
    ("tests/test_native_preflight_initrd.py::"
     "test_fifo_and_nonregular_input_are_rejected_without_waiting"): ("Windows",),
    ("tests/test_native_preflight_initrd.py::"
     "test_wheel_basename_collision_and_destination_reuse_fail"): ("Windows",),
    ("tests/test_native_preflight_initrd.py::"
     "test_file_and_total_input_bounds_are_enforced"): ("Windows",),
    ("tests/test_native_slot_owner_preflight.py::"
     "test_freeze_preserves_exact_bytes_and_rejects_replacement"): ("Windows",),
    ("tests/test_native_slot_owner_preflight.py::"
     "test_freeze_rejects_wrong_digest"): ("Windows",),
    ("tests/test_native_slot_owner_preflight.py::"
     "test_freeze_rejects_fifo_without_waiting_for_writer"): ("Windows",),
    ("tests/test_native_slot_owner_preflight.py::"
     "test_handoff_accepts_native_waitall_flag_and_transfers_one_socket"): ("Windows",),
    ("tests/test_native_slot_owner_preflight.py::"
     "test_owner_validates_separate_observation_over_control_frames"): ("Windows",),
    ("tests/test_native_guest_inputs.py::"
     "test_wheel_inventory_does_not_follow_symlinks"): ("Windows",),
    ("tests/test_linux_role_launcher.py::"
     "test_symlinked_runtime_source_is_rejected"): ("Windows",),
    ("tests/test_native_provider_bridge.py::"
     "test_child_reaper_records_actual_waitpid_exit_status_without_kill"): ("Windows",),
    ("tests/test_native_provider_bridge.py::"
     "test_host_failure_log_projection_exports_only_finite_error_categories"): ("Windows",),
    ("tests/test_native_provider_bridge.py::"
     "test_host_failure_log_projection_rejects_missing_or_unsafe_log"): ("Windows",),
    ("tests/test_maintenance_host_runner.py::"
     "test_context_snapshot_exports_exact_context_and_strictly_reopens"): ("Windows",),
    ("tests/test_maintenance_host_runner.py::"
     "test_evidence_files_bind_original_bytes_without_reading_other_case_files"): ("Windows",),
    ("tests/test_maintenance_host_runner.py::"
     "test_evidence_file_rejection_preserves_original_failure_and_files"): ("Windows",),
    ("tests/test_maintenance_host_runner.py::"
     "test_missing_original_evidence_never_uses_memory_as_file_bytes"): ("Windows",),
    ("tests/test_maintenance_host_runner.py::"
     "test_pre_dispatch_failure_does_not_read_existing_case_path"): ("Windows",),
    ("tests/test_maintenance_host_runner.py::"
     "test_evidence_binding_rejects_case_directory_escape"): ("Windows",),
    ("tests/test_maintenance_context_snapshot.py::"
     "test_actual_public_context_reopens_without_changing_bytes_modes_or_receipt"): ("Windows",),
    ("tests/test_maintenance_context_snapshot.py::"
     "test_context_snapshot_rejects_cross_binding"): ("Windows",),
    ("tests/test_maintenance_context_snapshot.py::"
     "test_context_receipt_is_closed_even_when_its_digest_is_recomputed"): ("Windows",),
    ("tests/test_maintenance_context_snapshot.py::"
     "test_original_capsule_is_reverified_instead_of_trusting_declared_validity"): ("Windows",),
    ("tests/test_maintenance_context_snapshot.py::"
     "test_declared_validity_cannot_replace_current_verifier"): ("Windows",),
    ("tests/test_maintenance_context_snapshot.py::"
     "test_context_snapshot_rejects_drift_and_extra_material"): ("Windows",),
    ("tests/test_maintenance_context_snapshot.py::"
     "test_context_snapshot_rejects_unsafe_files_without_removing_them"): ("Windows",),
    ("tests/test_maintenance_run_evidence.py::"
     "test_public_run_recomputes_honest_failure_and_unknown_without_native_authority"):
        ("Windows",),
    ("tests/test_maintenance_run_evidence.py::"
     "test_run_evidence_rejects_expected_identity_drift"): ("Windows",),
    ("tests/test_maintenance_run_evidence.py::"
     "test_original_run_evidence_rejects_incomplete_or_drifted_bytes"): ("Windows",),
    ("tests/test_maintenance_run_evidence.py::"
     "test_run_evidence_rejects_unsafe_raw_files_without_removing_them"): ("Windows",),
    ("tests/test_maintenance_run_evidence.py::"
     "test_run_evidence_rejects_replacement_between_reads"): ("Windows",),
    ("tests/test_maintenance_run_evidence.py::"
     "test_run_evidence_rejects_claimed_success_for_unknown"): ("Windows",),
    ("tests/test_native_preflight_initrd.py::"
     "test_model_probe_requires_successor_manifest_and_explicit_nonce"): ("Windows",),
    ("tests/test_maintenance_fixture_snapshot.py::"
     "test_snapshot_rejects_fifo_without_waiting_for_a_writer"): ("Windows",),
    ("tests/test_maintenance_fixture_snapshot.py::"
     "test_frozen_fixture_reopens_strictly_and_preserves_every_source_file"): ("Windows",),
    ("tests/test_maintenance_fixture_snapshot.py::"
     "test_read_only_snapshot_manifest_is_closed_and_exact"): ("Windows",),
    ("tests/test_maintenance_fixture_snapshot.py::"
     "test_read_only_snapshot_rejects_unsafe_or_unregistered_bytes"): ("Windows",),
    ("tests/test_maintenance_fixture_snapshot.py::"
     "test_read_only_snapshot_rejects_integrity_valid_private_mutation_key"): ("Windows",),
    ("tests/test_maintenance_fixture_snapshot.py::"
     "test_wrong_identity_or_existing_destination_is_rejected_without_writes"): ("Windows",),
    ("tests/test_maintenance_fixture_snapshot.py::"
     "test_fixture_freeze_preserves_actual_post_outcome_run_and_feedback"): ("Windows",),
    ("tests/test_maintenance_fixture_snapshot.py::"
     "test_fixture_mutation_closure_accepts_only_fixed_bindings"): ("Windows",),
    ("tests/test_maintenance_fixture_snapshot.py::"
     "test_active_grant_is_rejected_instead_of_stripping_its_token"): ("Windows",),
    ("tests/test_maintenance_fixture_snapshot.py::"
     "test_private_body_with_valid_governance_is_rejected"): ("Windows",),
    ("tests/test_maintenance_fixture_snapshot.py::"
     "test_unexpected_writer_is_rejected"): ("Windows",),
    ("tests/test_maintenance_fixture_snapshot.py::"
     "test_unsafe_or_unreferenced_material_is_rejected"): ("Windows",),
    ("tests/test_native_slot_owner_preflight.py::"
     "test_authority_binds_only_after_exact_guest_execution_challenge"): ("Windows",),
}

NATIVE_NONAPPLICABLE_TESTS.update({
    f"tests/test_maintenance_bundle_attachment.py::{name}": ("Windows",)
    for name in (
        "test_stage_public_run_copies_exact_original_closure_and_reopens",
        "test_stage_accepts_all_honest_failures_including_unknown",
        "test_stage_rejects_unsafe_source_and_preserves_evidence",
        "test_stage_rejects_destination_reuse_and_preserves_existing_bytes",
        "test_stage_rejects_replacement_while_output_descriptor_is_open",
        "test_stage_rejects_same_byte_replacement_during_copy_and_retains_partial_output",
    )
})
NATIVE_NONAPPLICABLE_TESTS.update({
    f"tests/test_owner_provider_authority.py::{name}": ("Windows",)
    for name in (
        "test_original_body_response_hash_closed_environment_and_fresh_binding",
        "test_profiles_are_frozen_and_budget_consumed_before_send",
        "test_blocked_open_is_forcibly_killed_and_reaped_at_request_deadline",
        "test_absolute_instance_deadline_kills_even_without_an_active_reader",
        "test_ambiguous_callback_exit_and_response_failure_are_terminal_without_replay",
        "test_response_limit_uses_the_existing_finite_header_and_content_type_codec",
        "test_request_limit_and_overflow_do_not_invoke_callback",
        "test_child_rejects_replayed_sequence_without_a_second_callback",
        "test_child_enforces_profile_budget_independently",
        "test_cleanup_failure_retains_first_failure_and_still_reaps",
        "test_stop_reply_without_child_exit_is_unconfirmed_and_forcibly_reaped",
        "test_one_parent_reader_concurrent_forward_kills_authority_without_replay",
        "test_private_entry_is_exact_owner_only_and_paths_do_not_escape",
        "test_startup_deadline_kills_and_reaps_child_before_any_request",
        "test_pipe_setup_failure_still_kills_reaps_and_closes_started_child",
        "test_repeated_start_is_terminal_and_reaps_original_authority",
        "test_exited_leader_cannot_leave_same_group_forward_descendant_running",
        "test_group_observation_failure_preserves_first_failure_and_cannot_confirm_cleanup",
        "test_truncated_response_header_is_unknown_terminal_and_reaped",
        "test_bad_pipe_header_fails_closed_without_payload_diagnostic",
        "test_no_inherited_authentication_environment",
    )
})


class PlatformManifestFreezeError(ValueError):
    """Raised when collection cannot be represented without ambiguity."""


def _sha256(value: Any) -> str:
    return sha256_bytes(canonical_json(value).encode("utf-8"))


def _historical_descriptors() -> dict[str, dict[str, str]]:
    value = json.loads(HISTORICAL_MANIFEST.read_text(encoding="utf-8"))
    cases = [
        *value["inventories"]["common"]["cases"],
        *value["inventories"]["windows"]["additional_cases"],
        *value["classifications"]["qualification"]["cases"],
    ]
    return {str(case["node_id"]): dict(case["junit"]) for case in cases}


def _descriptor(node_id: str, historical: dict[str, dict[str, str]]) -> dict[str, Any]:
    known = historical.get(node_id)
    if known is not None:
        return {"node_id": node_id, "junit": known}
    path, separator, name = node_id.partition("::")
    if separator != "::" or not path.endswith(".py") or not name or "::" in name:
        raise PlatformManifestFreezeError(
            f"new node ID requires an explicit JUnit descriptor: {node_id}"
        )
    return {
        "node_id": node_id,
        "junit": {
            "classname": path[:-3].replace("/", "."),
            "name": name,
        },
    }


def build_manifest(repository: Path = REPOSITORY) -> dict[str, Any]:
    """Collect and freeze the v2 common, Windows, and qualification inventories."""

    root = repository.resolve(strict=True)
    historical = _historical_descriptors()
    common_ids = collect_node_ids(root, selection="common")
    windows_ids = collect_node_ids(root, selection="windows")
    qualification_ids = collect_node_ids(root, selection="qualification")
    common_set = set(common_ids)
    windows_set = set(windows_ids)
    qualification_set = set(qualification_ids)
    if not common_set < windows_set:
        raise PlatformManifestFreezeError(
            "Windows inventory must be a strict superset of common inventory"
        )
    if common_set & qualification_set or windows_set & qualification_set:
        raise PlatformManifestFreezeError(
            "Platform Core and qualification classifications overlap"
        )
    additional_ids = sorted(windows_set - common_set)
    common = [_descriptor(node_id, historical) for node_id in common_ids]
    additional = [_descriptor(node_id, historical) for node_id in additional_ids]
    if len(POSIX_ONLY_ON_WINDOWS_NODE_IDS) != len(set(POSIX_ONLY_ON_WINDOWS_NODE_IDS)):
        raise PlatformManifestFreezeError(
            "POSIX-only-on-Windows cases contain duplicate node IDs"
        )
    if set(POSIX_ONLY_ON_WINDOWS_NODE_IDS) & NATIVE_NONAPPLICABLE_TESTS.keys():
        raise PlatformManifestFreezeError("nonapplicable declarations overlap")
    declared = {
        **dict.fromkeys(POSIX_ONLY_ON_WINDOWS_NODE_IDS, ("Windows",)),
        **NATIVE_NONAPPLICABLE_TESTS,
    }
    nonapplicable: dict[str, dict[str, Any]] = {
        case["node_id"]: {**case, "nonapplicable_systems": ["Linux", "Darwin"]}
        for case in additional
    }
    for test_id, systems in declared.items():
        matched = [
            node_id for node_id in common_ids
            if node_id == test_id or node_id.startswith(test_id + "[")
        ]
        if not matched:
            raise PlatformManifestFreezeError(
                "declared nonapplicable test is absent from common inventory: " + test_id
            )
        for node_id in matched:
            if node_id in nonapplicable:
                raise PlatformManifestFreezeError(
                    "nonapplicable declarations overlap: " + node_id
                )
            nonapplicable[node_id] = {
                **_descriptor(node_id, historical),
                "nonapplicable_systems": list(systems),
            }
    qualification = [
        _descriptor(node_id, historical) for node_id in qualification_ids
    ]
    historical_case = next(
        (case for case in common if case["node_id"] == HISTORICAL_COMPATIBILITY_NODE_ID),
        None,
    )
    if historical_case is None:
        raise PlatformManifestFreezeError(
            "frozen historical compatibility case is absent from Platform Core"
        )
    manifest: dict[str, Any] = {
        "schema_version": "deeplaw.platform-core-test-manifest/v2",
        "selection": {
            "common": "not qualification and not windows_native",
            "windows": "not qualification",
            "qualification": "qualification",
            "windows_native": "windows_native",
        },
        "generation": {
            "commands": [
                "uv run --frozen pytest --collect-only -q -o addopts='' "
                "-m 'not qualification and not windows_native'",
                "uv run --frozen pytest --collect-only -q -o addopts='' "
                "-m 'not qualification'",
                "uv run --frozen pytest --collect-only -q -o addopts='' "
                "-m 'qualification'",
            ],
            "pytest": "pytest",
            "selection_source": "repository test collection",
        },
        "inventories": {
            "common": {
                "selection": "not qualification and not windows_native",
                "count": len(common),
                "sha256": _sha256(common),
                "cases": common,
            },
            "windows": {
                "selection": "not qualification",
                "extends": "common",
                "count": len(common) + len(additional),
                "sha256": _sha256([*common, *additional]),
                "additional_cases": additional,
            },
        },
        "classifications": {
            "qualification": {
                "selection": "qualification",
                "status": "not_executed",
                "cases": qualification,
            },
            "nonapplicable": {
                "selection": "platform-specific tests outside their applicable OS",
                "status": "nonapplicable",
                "cases": sorted(
                    nonapplicable.values(),
                    key=lambda case: case["node_id"],
                ),
            },
            "historical_compatibility": {
                "selection": "exact frozen v0.6 wheel required",
                "status": "required_fixture",
                "cases": [historical_case],
            },
        },
    }
    manifest["manifest_sha256"] = _sha256(manifest)
    return manifest


def _main() -> int:
    parser = argparse.ArgumentParser(description="Freeze Platform Core v2")
    parser.add_argument(
        "--output",
        type=Path,
        default=REPOSITORY / "benchmarks/release/platform-core-test-manifest-v2.json",
    )
    args = parser.parse_args()
    manifest = build_manifest()
    args.output.write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(_main())
