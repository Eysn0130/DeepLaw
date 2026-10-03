from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from jsonschema import Draft202012Validator, FormatChecker

from benchmarks.v013 import scale_qualification_v9 as v9
from benchmarks.v013 import scale_qualification_v10 as v10
from deeplaw.api import KnowledgeOS
from deeplaw.compilation.models import MAX_COMPILATION_REQUEST_BYTES
from deeplaw.knowledge_autonomy import AutonomousKnowledgeStore, initialize_autonomous_core
from deeplaw.knowledge_compiler import compile_source
from deeplaw.knowledge_store import KnowledgeVault, initialize_knowledge_vault

ROOT = Path(__file__).resolve().parents[1]


def _candidate() -> dict[str, object]:
    return {
        "commit": "1" * 40,
        "tree": "2" * 40,
        "version": "0.13.0",
        "lock_sha256": "3" * 64,
        "wheel": {
            "filename": "deeplaw-0.13.0-py3-none-any.whl",
            "sha256": "4" * 64,
            "size_bytes": 123,
        },
        "sdist": {
            "filename": "deeplaw-0.13.0.tar.gz",
            "sha256": "5" * 64,
            "size_bytes": 456,
        },
    }


def _run() -> dict[str, object]:
    started = datetime.now(UTC).replace(microsecond=0)
    finished = started + timedelta(seconds=2)
    return {
        "run_id": "scale-v10-test-run",
        "workflow_run_id": 13,
        "started_at_utc": started.isoformat().replace("+00:00", "Z"),
        "finished_at_utc": finished.isoformat().replace("+00:00", "Z"),
        "platform": "Darwin-test-arm64",
        "python_version": "3.12.0",
        "runner": v10.RUNNER_RELATIVE_PATH,
        "runner_sha256": "6" * 64,
        "command": (
            "uv run --frozen python -m benchmarks.v013.scale_qualification_v10 "
            "--execute-10k"
        ),
    }


def _digest(value: object) -> str:
    return hashlib.sha256(
        json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()


def _query_context() -> dict[str, object]:
    profile = v10.V10_PROFILE
    provider_samples = [1000, 2000] + [1000] * 60
    query_bytes = [provider_samples[0], *provider_samples[2::2]]
    context_bytes = [provider_samples[1], *provider_samples[3::2]]

    def surface(bytes_by_sample: list[int]) -> dict[str, object]:
        return {
            "sample_count": 31,
            "warmup_count": 1,
            "measured_sample_count": 30,
            "plan_schema_versions": [profile.query_plan_schema_version] * 31,
            "query_plan_sha256": ["d" * 64] * 31,
            "provider_schema_versions": [profile.provider_schema_version] * 31,
            "provider_inner_schema_versions": [
                profile.provider_inner_schema_version
            ]
            * 31,
            "provider_content_bytes": bytes_by_sample,
            "provider_inner_sha256": ["e" * 64] * 31,
            "source_binding_sha256": ["f" * 64] * 31,
            "source_ref_counts": [1] * 31,
            "selected_semantic_keys": [profile.semantic_key] * 31,
            "write_performed": [False] * 31,
        }

    return {
        "schema_version": profile.query_context_observation_schema,
        "query": surface(query_bytes),
        "context": surface(context_bytes),
    }


def _semantic_batches() -> list[dict[str, object]]:
    return [
        {
            "batch_index": index,
            "global_offset": index * v9.FRAGMENTS_PER_SOURCE,
            "target_object_count": v9.FRAGMENTS_PER_SOURCE,
            "grant_max_objects": (index + 1) * v9.FRAGMENTS_PER_SOURCE,
            "grant_id": f"grant-v10-{index:03d}",
            "compilation_run_id": f"run-v10-{index:03d}",
            "source_revision_id": f"source-v10-{index:03d}",
            "asset_count": v9.FRAGMENTS_PER_SOURCE,
            "asset_ids_sha256": "a" * 64,
            "publication_request_bytes": 1000,
            "publication_request_sha256": "b" * 64,
            "publication_request_limit_bytes": MAX_COMPILATION_REQUEST_BYTES,
            "published_object_count": v9.FRAGMENTS_PER_SOURCE,
            "committed_object_count": v9.FRAGMENTS_PER_SOURCE,
            "committed_relation_count": 0,
        }
        for index in range(v9.SOURCE_BATCH_COUNT)
    ]


def _report() -> dict[str, object]:
    digest = "8" * 64
    provider_samples = [1000, 2000] + [1000] * 60
    return v10.build_scale_qualification_report(
        candidate_binding=_candidate(),
        run_binding=_run(),
        active_governed_object_count=v9.ACTIVE_GOVERNED_OBJECT_TARGET,
        query_samples_ms=[float(index + 1) for index in range(v9.WARM_SAMPLE_TARGET)],
        context_samples_ms=[float(index + 2) for index in range(v9.WARM_SAMPLE_TARGET)],
        query_warmup={
            "elapsed_ms": 1.0,
            "sample_count": 1,
            "excluded_from_measured_samples": True,
            "provider_payload_bytes": 1000,
        },
        context_warmup={
            "elapsed_ms": 2.0,
            "sample_count": 1,
            "excluded_from_measured_samples": True,
            "provider_payload_bytes": 2000,
        },
        rss={"start_bytes": 100, "peak_bytes": 120, "end_bytes": 110},
        storage_bytes=1024,
        file_count=8,
        build_duration_ms=10.0,
        rebuild_duration_ms=11.0,
        equivalence={
            "full": {"sha256": digest},
            "incremental": {"sha256": digest},
            "no_op": {"sha256": digest},
            "full_incremental_equal": True,
            "incremental_noop_equal": True,
            "exact": True,
        },
        rebuild={
            "full": {
                "mode": "full",
                "projection_profile": "standard",
                "change_counts": {"created": 10000, "updated": 0, "deleted": 0, "unchanged": 0},
                "stable_identity_sha256": digest,
            },
            "minimal": {
                "mode": "minimal",
                "projection_profile": "minimal",
                "change_counts": {"created": 0, "updated": 0, "deleted": 0, "unchanged": 10000},
                "stable_identity_sha256": "9" * 64,
            },
            "incremental": {
                "mode": "incremental",
                "projection_profile": "standard",
                "change_counts": {"created": 0, "updated": 10000, "deleted": 0, "unchanged": 0},
                "stable_identity_sha256": digest,
            },
            "no_op": {
                "mode": "no_op",
                "projection_profile": "standard",
                "change_counts": {"created": 0, "updated": 0, "deleted": 0, "unchanged": 10000},
                "stable_identity_sha256": digest,
            },
        },
        source_compile={
            "source_file_count": v9.SOURCE_BATCH_COUNT,
            "fragments_per_source": v9.FRAGMENTS_PER_SOURCE,
            "expected_asset_count": v9.ACTIVE_GOVERNED_OBJECT_TARGET,
            "asset_count": v9.ACTIVE_GOVERNED_OBJECT_TARGET,
            "unique_asset_count": v9.ACTIVE_GOVERNED_OBJECT_TARGET,
            "asset_ids_sha256": "c" * 64,
            "exact": True,
        },
        query_context=_query_context(),
        semantic_batches=_semantic_batches(),
        user_files=[
            {
                "relative_path": "user-owned.md",
                "size_before": 12,
                "size_after": 12,
                "sha256_before": "7" * 64,
                "sha256_after": "7" * 64,
                "unchanged": True,
            }
        ],
        provider_sample_bytes=provider_samples,
    )


def test_v10_profile_exposes_current_contract_and_rejects_unknown_versions() -> None:
    assert v10.SCHEMA_VERSION == "deeplaw.v013-scale-qualification-report/v10"
    assert v10.QUERY_CONTEXT_OBSERVATION_SCHEMA == (
        "deeplaw.v013-scale-query-context-observation/v2"
    )
    assert v10.QUERY_PLAN_SCHEMA_V7 == "deeplaw.knowledge-query-plan/v7"
    assert v10.PROVIDER_CAPSULE_SCHEMA_V3 == "deeplaw.provider-knowledge-capsule/v3"
    assert v10.PROVIDER_INNER_SCHEMA_V2 == "deeplaw.knowledge-capsule-projection/v2"
    assert v9.profile_for_version("v10") is v10.V10_PROFILE
    with pytest.raises(v9.ScaleQualificationError, match="unsupported"):
        v9.profile_for_version("v11")


def test_v10_report_uses_v10_schema_and_v9_verifier_rejects_it() -> None:
    report = _report()
    schema = json.loads(
        (ROOT / "contracts/v013-scale-qualification-report.v10.schema.json").read_text(
            encoding="utf-8"
        )
    )
    errors = list(Draft202012Validator(schema, format_checker=FormatChecker()).iter_errors(report))
    assert errors == []
    assert v10.verify_report(report) == {"valid": True, "errors": []}
    assert v9.verify_report(report)["valid"] is False
    assert report["query_context"]["schema_version"] == v10.QUERY_CONTEXT_OBSERVATION_SCHEMA


def test_v10_query_context_observation_rejects_v6_and_v9_rejects_v7() -> None:
    report = _report()
    report["query_context"]["query"]["plan_schema_versions"][0] = v9.QUERY_PLAN_SCHEMA_V6
    assert v10.verify_report(report)["valid"] is False
    report = _report()
    report["query_context"]["query"]["provider_schema_versions"][0] = (
        v9.PROVIDER_CAPSULE_SCHEMA_V2
    )
    assert v10.verify_report(report)["valid"] is False


def test_v10_public_batch_smoke_uses_current_v7_provider3_and_inner2(tmp_path: Path) -> None:
    vault = tmp_path / "Vault"
    initialize_knowledge_vault(vault, name="v013-v10-smoke", scope="project")
    source = tmp_path / "smoke.md"
    source.write_text(
        "\n".join(
            f"# Smoke 000-{index:03d}\nPublic v10 batch evidence 000-{index:03d}."
            for index in range(2)
        )
        + "\n",
        encoding="utf-8",
        newline="\n",
    )
    with KnowledgeVault(vault, read_only=False) as legacy:
        source_result = compile_source(
            legacy,
            source,
            source_kind="document",
            sensitivity="public",
            confirm_no_case_data=True,
            logical_path="smoke.md",
        )
    initialize_autonomous_core(vault)
    with KnowledgeOS.open(vault) as knowledge_os:
        receipt = v10._public_semantic_compile(
            vault,
            source_result,
            target=2,
            global_offset=0,
            batch_index=0,
            knowledge_os_handle=knowledge_os,
        )
    assert receipt["published_object_count"] == 2
    with AutonomousKnowledgeStore(vault, read_only=False) as store:
        store.rebuild_derived(projection_profile="standard")
    with KnowledgeOS.open(vault) as knowledge_os:
        measured = v10._measure_query_context(
            knowledge_os,
            query_text="Smoke 000-000",
        )
    assert measured["query_context"]["query"]["plan_schema_versions"] == [
        v10.QUERY_PLAN_SCHEMA_V7
    ] * 31
    assert measured["query_context"]["context"]["provider_schema_versions"] == [
        v10.PROVIDER_CAPSULE_SCHEMA_V3
    ] * 31
    assert measured["query_context"]["context"]["provider_inner_schema_versions"] == [
        v10.PROVIDER_INNER_SCHEMA_V2
    ] * 31
