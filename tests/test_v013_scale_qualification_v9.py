from __future__ import annotations

import hashlib
import json
from copy import deepcopy
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from jsonschema import Draft202012Validator, FormatChecker

from benchmarks.release.typed_qualification_evidence import (
    TypedQualificationEvidenceError,
    parse_typed_evidence,
)
from benchmarks.v013.scale_qualification_v9 import (
    ACTIVE_GOVERNED_OBJECT_TARGET,
    FRAGMENTS_PER_SOURCE,
    HARD_FAILURE_IDS,
    PROVIDER_HARD_LIMIT_BYTES,
    SCHEMA_RELATIVE_PATH,
    SOURCE_BATCH_COUNT,
    WARM_MAX_CEILING_MS,
    WARM_P95_CEILING_MS,
    WARM_SAMPLE_TARGET,
    ScaleQualificationError,
    _change_counts,
    _equivalence_digest,
    _public_semantic_compile,
    build_scale_qualification_report,
    verify_report,
)
from deeplaw.api import KnowledgeOS
from deeplaw.compilation.models import MAX_COMPILATION_REQUEST_BYTES
from deeplaw.knowledge_autonomy import AutonomousKnowledgeStore, initialize_autonomous_core
from deeplaw.knowledge_compiler import compile_source
from deeplaw.knowledge_store import KnowledgeVault, initialize_knowledge_vault

ROOT = Path(__file__).resolve().parents[1]


def _digest(value: object) -> str:
    return hashlib.sha256(
        json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()


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
        "run_id": "scale-v9-test-run",
        "workflow_run_id": 13,
        "started_at_utc": started.isoformat().replace("+00:00", "Z"),
        "finished_at_utc": finished.isoformat().replace("+00:00", "Z"),
        "platform": "Darwin-test-arm64",
        "python_version": "3.12.0",
        "runner": "benchmarks/v013/scale_qualification_v9.py",
        "runner_sha256": "6" * 64,
        "command": "uv run --frozen python -m benchmarks.v013.scale_qualification_v9 --execute-10k",
    }


def _user_file() -> dict[str, object]:
    return {
        "relative_path": "user-owned.md",
        "size_before": 12,
        "size_after": 12,
        "sha256_before": "7" * 64,
        "sha256_after": "7" * 64,
        "unchanged": True,
    }


def _equivalence() -> dict[str, object]:
    digest = "8" * 64
    return {
        "full": {"sha256": digest},
        "incremental": {"sha256": digest},
        "no_op": {"sha256": digest},
        "full_incremental_equal": True,
        "incremental_noop_equal": True,
        "exact": True,
    }


def _semantic_batches() -> list[dict[str, object]]:
    batches: list[dict[str, object]] = []
    for index in range(SOURCE_BATCH_COUNT):
        batches.append(
            {
                "batch_index": index,
                "global_offset": index * FRAGMENTS_PER_SOURCE,
                "target_object_count": FRAGMENTS_PER_SOURCE,
                "grant_max_objects": (index + 1) * FRAGMENTS_PER_SOURCE,
                "grant_id": f"grant-v9-{index:03d}",
                "compilation_run_id": f"run-v9-{index:03d}",
                "source_revision_id": f"source-v9-{index:03d}",
                "asset_count": FRAGMENTS_PER_SOURCE,
                "asset_ids_sha256": "a" * 64,
                "publication_request_bytes": 1000,
                "publication_request_sha256": "b" * 64,
                "publication_request_limit_bytes": MAX_COMPILATION_REQUEST_BYTES,
                "published_object_count": FRAGMENTS_PER_SOURCE,
                "committed_object_count": FRAGMENTS_PER_SOURCE,
                "committed_relation_count": 0,
            }
        )
    return batches


def _rebuild() -> dict[str, object]:
    digest = "8" * 64
    return {
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
    }


def _source_compile() -> dict[str, object]:
    return {
        "source_file_count": SOURCE_BATCH_COUNT,
        "fragments_per_source": FRAGMENTS_PER_SOURCE,
        "query_plan_version": "5",
        "expected_asset_count": ACTIVE_GOVERNED_OBJECT_TARGET,
        "asset_count": ACTIVE_GOVERNED_OBJECT_TARGET,
        "unique_asset_count": ACTIVE_GOVERNED_OBJECT_TARGET,
        "asset_ids_sha256": "c" * 64,
        "exact": True,
    }


def _report(
    *,
    count: int = ACTIVE_GOVERNED_OBJECT_TARGET,
    query_count: int = WARM_SAMPLE_TARGET,
    context_count: int = WARM_SAMPLE_TARGET,
    query_samples_ms: list[float] | None = None,
    context_samples_ms: list[float] | None = None,
    provider_samples: list[int] | None = None,
) -> dict[str, object]:
    observed_query_samples = query_samples_ms or [
        float(index + 1) for index in range(query_count)
    ]
    observed_context_samples = context_samples_ms or [
        float(index + 2) for index in range(context_count)
    ]
    observed_provider_samples = (
        [1000] * 62
        if provider_samples is None
        else [1000] * 61 + provider_samples
        if len(provider_samples) == 1
        else provider_samples
    )
    return build_scale_qualification_report(
        candidate_binding=_candidate(),
        run_binding=_run(),
        active_governed_object_count=count,
        query_samples_ms=observed_query_samples,
        context_samples_ms=observed_context_samples,
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
        equivalence=_equivalence(),
        rebuild=_rebuild(),
        source_compile=_source_compile(),
        semantic_batches=_semantic_batches(),
        user_files=[_user_file()],
        provider_sample_bytes=observed_provider_samples,
    )


def _redigest(report: dict[str, object]) -> dict[str, object]:
    report["report_sha256"] = _digest(
        {key: value for key, value in report.items() if key != "report_sha256"}
    )
    return report


def _typed_scale_manifest(
    tmp_path: Path,
    *,
    report: dict[str, object] | None = None,
) -> Path:
    observed = report or _report()
    expected = {
        "schema_version": "deeplaw.v013-scale-qualification-expected/v9",
        "active_governed_object_count": ACTIVE_GOVERNED_OBJECT_TARGET,
        "source_file_count": SOURCE_BATCH_COUNT,
        "fragments_per_source": FRAGMENTS_PER_SOURCE,
        "query_plan_version": "5",
        "warm_samples": WARM_SAMPLE_TARGET,
        "provider_hard_limit_bytes": PROVIDER_HARD_LIMIT_BYTES,
        "above_10000_status": "experimental_unqualified",
        "deferred_100000": "v0.14",
    }

    def source(relative_path: str, value: object) -> dict[str, object]:
        raw = json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode()
        (tmp_path / relative_path).write_bytes(raw)
        return {
            "relative_path": relative_path,
            "byte_size": len(raw),
            "sha256": hashlib.sha256(raw).hexdigest(),
            "media_type": "application/json",
        }

    expected_source = source("scale-expected.json", expected)
    observed_source = source("scale-observed.json", observed)
    candidate = observed["candidate_binding"]
    run = observed["run_binding"]
    envelope: dict[str, object] = {
        "schema_version": "deeplaw.typed-qualification-evidence/v3",
        "profile": "kernel_release_core",
        "reference_provenance": "deterministic_expected_evidence",
        "human_authenticity": "not_claimed",
        "kind": "scale_report",
        "candidate_binding": {
            "commit": candidate["commit"],
            "tree": candidate["tree"],
            "lock_sha256": candidate["lock_sha256"],
            "wheel_sha256": candidate["wheel"]["sha256"],
            "sdist_sha256": candidate["sdist"]["sha256"],
        },
        "run_binding": {
            "run_id": run["run_id"],
            "workflow_run_id": run["workflow_run_id"],
        },
        "corpus": {"sha256": expected_source["sha256"], "role": "scale_10000"},
        "runner": {
            "identity": "runner:scale-v9",
            "sha256": run["runner_sha256"],
        },
        "scorer": {"identity": "scorer:scale-v9", "sha256": "a" * 64},
        "payload": {
            "expected_source": expected_source,
            "observed_source": observed_source,
        },
        "record_sha256": "",
    }
    envelope["record_sha256"] = _digest(
        {key: value for key, value in envelope.items() if key != "record_sha256"}
    )
    manifest = tmp_path / "scale-manifest.json"
    manifest.write_text(
        json.dumps(envelope, ensure_ascii=False, sort_keys=True, separators=(",", ":")),
        encoding="utf-8",
    )
    return manifest


def _public_batch_smoke(
    tmp_path: Path, *, batch_count: int, fragments_per_source: int
) -> list[dict[str, object]]:
    vault = tmp_path / "Vault"
    initialize_knowledge_vault(vault, name="v013-batch-smoke", scope="project")
    sources = []
    for batch_index in range(batch_count):
        source = tmp_path / f"smoke-{batch_index:03d}.md"
        source.write_text(
            "\n".join(
                f"# Smoke {batch_index:03d}-{fragment_index:03d}\n"
                f"Public batch evidence {batch_index:03d}-{fragment_index:03d}."
                for fragment_index in range(fragments_per_source)
            )
            + "\n",
            encoding="utf-8",
            newline="\n",
        )
        sources.append(source)
    source_results = []
    asset_ids: list[str] = []
    with KnowledgeVault(vault, read_only=False) as legacy:
        for source_index, source in enumerate(sources):
            result = compile_source(
                legacy,
                source,
                source_kind="document",
                sensitivity="public",
                confirm_no_case_data=True,
                logical_path=f"smoke-{source_index:03d}.md",
            )
            source_results.append(result)
            asset_ids.extend(result["asset_ids"])
    initialize_autonomous_core(vault)
    receipts = []
    with KnowledgeOS.open(vault) as knowledge_os:
        for batch_index, source_result in enumerate(source_results):
            receipts.append(
                _public_semantic_compile(
                    vault,
                    source_result,
                    target=fragments_per_source,
                    global_offset=batch_index * fragments_per_source,
                    batch_index=batch_index,
                    knowledge_os_handle=knowledge_os,
                )
            )
    assert len(asset_ids) == batch_count * fragments_per_source
    assert len(set(asset_ids)) == len(asset_ids)
    return receipts


def test_v9_scale_schema_is_strict_and_valid_report_has_exact_10k_contract() -> None:
    schema = json.loads((ROOT / SCHEMA_RELATIVE_PATH).read_text(encoding="utf-8"))
    report = _report()
    errors = list(Draft202012Validator(schema, format_checker=FormatChecker()).iter_errors(report))
    assert errors == []
    assert verify_report(report) == {"valid": True, "errors": []}
    assert report["vault"]["active_governed_object_count"] == ACTIVE_GOVERNED_OBJECT_TARGET
    assert report["warm_samples"]["query"]["sample_count"] == WARM_SAMPLE_TARGET
    assert report["warm_samples"]["context"]["sample_count"] == WARM_SAMPLE_TARGET
    assert report["provider"]["hard_limit_bytes"] == PROVIDER_HARD_LIMIT_BYTES
    assert "warmup" in report["warm_samples"]["query"]


def test_v9_scale_count_must_be_exactly_10k_and_over_10k_is_not_qualified() -> None:
    for count in (9_999, 10_001, 100_000):
        report = _report(count=count)
        assert report["release_gate_passed"] is False
        assert "active_governed_object_count_mismatch" in report["hard_failures"]
        assert verify_report(report) == {"valid": True, "errors": []}
    report = _report()
    report["vault"]["above_10000_status"] = "qualified"
    _redigest(report)
    checked = verify_report(report)
    assert checked["valid"] is False
    assert any("experimental_unqualified" in error for error in checked["errors"])


def test_v9_scale_requires_at_least_30_query_and_context_samples() -> None:
    with pytest.raises(ScaleQualificationError, match="exactly 30"):
        _report(query_count=29)
    with pytest.raises(ScaleQualificationError, match="exactly 30"):
        _report(context_count=29)

    slow = _report(
        query_samples_ms=[1.0] * 28 + [2_001.0, 2_001.0],
        context_samples_ms=[1.0] * 29 + [5_001.0],
    )
    assert slow["release_gate_passed"] is False
    assert {
        "query_p95_exceeded",
        "context_max_exceeded",
    } <= set(slow["hard_failures"])

    at_limit = _report(
        query_samples_ms=[1.0] * 28
        + [float(WARM_P95_CEILING_MS), float(WARM_P95_CEILING_MS)],
        context_samples_ms=[1.0] * 29 + [float(WARM_MAX_CEILING_MS)],
    )
    assert at_limit["release_gate_passed"] is True
    assert not {
        "query_p95_exceeded",
        "context_p95_exceeded",
        "query_max_exceeded",
        "context_max_exceeded",
    } & set(at_limit["hard_failures"])


def test_v9_scale_missing_metric_is_fail_closed() -> None:
    report = _report()
    del report["warm_samples"]["query"]["p95_ms"]
    assert verify_report(report)["valid"] is False

    report = _report()
    del report["warm_samples"]["context"]["warmup"]
    assert verify_report(report)["valid"] is False

    report = _report()
    report["warm_samples"]["query"]["warmup"]["sample_count"] = 2
    _redigest(report)
    assert verify_report(report)["valid"] is False


def test_v9_scale_equivalence_and_user_bytes_are_recomputed_not_claimed() -> None:
    report = _report()
    report["equivalence"]["no_op"]["sha256"] = "9" * 64
    _redigest(report)
    checked = verify_report(report)
    assert checked["valid"] is False
    assert any("hard_failures" in error for error in checked["errors"])

    tampered = _report()
    tampered["user_bytes"]["files"][0]["sha256_after"] = "a" * 64
    _redigest(tampered)
    checked = verify_report(tampered)
    assert checked["valid"] is False
    assert "user_bytes_unbounded" in checked["errors"][0] or any(
        "hard_failures" in error for error in checked["errors"]
    )


def test_v9_scale_rebuild_modes_and_change_counts_are_fail_closed() -> None:
    report = _report()
    report["rebuild"]["incremental"]["mode"] = "no_op"
    _redigest(report)
    checked = verify_report(report)
    assert checked["valid"] is False
    assert any("hard_failures" in error for error in checked["errors"])

    report = _report()
    report["semantic_batches"][0]["publication_request_bytes"] = (
        MAX_COMPILATION_REQUEST_BYTES + 1
    )
    _redigest(report)
    assert verify_report(report)["valid"] is False

    report = _report()
    report["semantic_batches"][1]["grant_max_objects"] = FRAGMENTS_PER_SOURCE
    _redigest(report)
    assert verify_report(report)["valid"] is False


def test_v9_scale_provider_bound_is_hard_and_zero_violation_is_required() -> None:
    report = _report(provider_samples=[PROVIDER_HARD_LIMIT_BYTES + 1])
    assert report["release_gate_passed"] is False
    assert report["provider"]["violation_count"] == 1
    assert "provider_bound_exceeded" in report["hard_failures"]
    assert verify_report(report) == {"valid": True, "errors": []}

    tampered = _report()
    tampered["provider"]["max_bytes"] = PROVIDER_HARD_LIMIT_BYTES + 1
    tampered["provider"]["violation"] = False
    _redigest(tampered)
    assert verify_report(tampered)["valid"] is False

    tampered = _report()
    tampered["provider"]["sample_count"] -= 1
    _redigest(tampered)
    assert verify_report(tampered)["valid"] is False


def test_v9_scale_report_digest_is_bound_to_exact_bytes() -> None:
    report = _report()
    tampered = deepcopy(report)
    tampered["metrics"]["file_count"] += 1
    assert verify_report(tampered)["valid"] is False


def test_v9_typed_scale_evidence_binds_report_and_derives_gate_vocabulary(
    tmp_path: Path,
) -> None:
    manifest = _typed_scale_manifest(tmp_path)
    expected_sha = json.loads(manifest.read_text())["corpus"]["sha256"]
    derived = parse_typed_evidence(
        manifest,
        expected_corpus_sha256=expected_sha,
    )
    assert derived["status"] == "passed"
    assert derived["metrics"]["p50"] == 16.0
    assert derived["metrics"]["full_incremental_noop_equivalence"] == 1
    assert derived["metrics"]["provider_bound"] == 1
    assert set(derived["hard_failure_counts"]) == {
        "scale_not_executed",
        *HARD_FAILURE_IDS,
    }
    assert not any(derived["hard_failure_counts"].values())


def test_v9_typed_scale_evidence_rejects_run_binding_substitution(tmp_path: Path) -> None:
    report = _report()
    report["run_binding"]["workflow_run_id"] = 99
    _redigest(report)
    manifest = _typed_scale_manifest(tmp_path, report=report)
    envelope = json.loads(manifest.read_text())
    envelope["run_binding"]["workflow_run_id"] = 13
    envelope["record_sha256"] = _digest(
        {key: value for key, value in envelope.items() if key != "record_sha256"}
    )
    manifest.write_text(
        json.dumps(envelope, ensure_ascii=False, sort_keys=True, separators=(",", ":")),
        encoding="utf-8",
    )
    with pytest.raises(TypedQualificationEvidenceError, match="run binding"):
        parse_typed_evidence(
            manifest,
            expected_corpus_sha256=envelope["corpus"]["sha256"],
        )


def test_v9_typed_scale_evidence_preserves_failed_report_as_failed(tmp_path: Path) -> None:
    manifest = _typed_scale_manifest(tmp_path, report=_report(count=9_999))
    envelope = json.loads(manifest.read_text())
    derived = parse_typed_evidence(
        manifest,
        expected_corpus_sha256=envelope["corpus"]["sha256"],
    )
    assert derived["status"] == "failed"
    assert derived["hard_failure_counts"]["active_governed_object_count_mismatch"] == 1

    slow_root = tmp_path / "slow"
    slow_root.mkdir()
    slow_manifest = _typed_scale_manifest(
        slow_root,
        report=_report(
            query_samples_ms=[1.0] * 28 + [2_001.0, 2_001.0],
            context_samples_ms=[1.0] * 29 + [5_001.0],
        ),
    )
    slow = parse_typed_evidence(
        slow_manifest,
        expected_corpus_sha256=json.loads(slow_manifest.read_text())["corpus"]["sha256"],
    )
    assert slow["hard_failure_counts"]["query_p95_exceeded"] == 1
    assert slow["hard_failure_counts"]["context_max_exceeded"] == 1
    assert slow["metrics"]["p95"] == 2_001.0
    assert slow["metrics"]["max"] == 5_001.0


def test_v9_public_batch_smoke_preserves_global_offset_and_exact_summary(tmp_path: Path) -> None:
    receipts = _public_batch_smoke(tmp_path, batch_count=2, fragments_per_source=2)
    assert [item["global_offset"] for item in receipts] == [0, 2]
    assert [item["grant_max_objects"] for item in receipts] == [2, 4]
    assert [item["batch_index"] for item in receipts] == [0, 1]
    assert [item["published_object_count"] for item in receipts] == [2, 2]
    assert sum(item["asset_count"] for item in receipts) == 4
    assert sum(item["committed_object_count"] for item in receipts) == 4
    assert all(item["committed_relation_count"] == 0 for item in receipts)


def test_v9_public_batch_smoke_measures_each_publication_request_bound(tmp_path: Path) -> None:
    receipts = _public_batch_smoke(tmp_path, batch_count=2, fragments_per_source=3)
    assert len(receipts) == 2
    assert all(
        0 < item["publication_request_bytes"] <= MAX_COMPILATION_REQUEST_BYTES
        and item["publication_request_limit_bytes"] == MAX_COMPILATION_REQUEST_BYTES
        for item in receipts
    )


def test_v9_public_batches_reuse_one_verified_knowledge_os_session(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    original_open = KnowledgeOS.open
    open_count = 0

    def counted_open(path: str | Path) -> KnowledgeOS:
        nonlocal open_count
        open_count += 1
        return original_open(path)

    monkeypatch.setattr(KnowledgeOS, "open", staticmethod(counted_open))
    receipts = _public_batch_smoke(tmp_path, batch_count=2, fragments_per_source=2)

    assert len(receipts) == 2
    assert open_count == 1


def test_v9_frozen_40_object_batch_stays_on_public_bounded_path(tmp_path: Path) -> None:
    receipts = _public_batch_smoke(
        tmp_path,
        batch_count=1,
        fragments_per_source=FRAGMENTS_PER_SOURCE,
    )
    assert receipts[0]["published_object_count"] == FRAGMENTS_PER_SOURCE
    assert receipts[0]["grant_max_objects"] == FRAGMENTS_PER_SOURCE
    assert receipts[0]["publication_request_bytes"] <= MAX_COMPILATION_REQUEST_BYTES


def test_v9_repeated_40_object_batches_keep_finalization_provider_bounded(
    tmp_path: Path,
) -> None:
    receipts = _public_batch_smoke(
        tmp_path,
        batch_count=6,
        fragments_per_source=FRAGMENTS_PER_SOURCE,
    )
    assert [item["global_offset"] for item in receipts] == [0, 40, 80, 120, 160, 200]
    assert all(
        0 < item["finalization_provider_bytes"] <= PROVIDER_HARD_LIMIT_BYTES
        for item in receipts
    )


def test_v9_100_observation_finalization_stays_on_public_bounded_path(
    tmp_path: Path,
) -> None:
    receipts = _public_batch_smoke(
        tmp_path,
        batch_count=1,
        fragments_per_source=100,
    )
    assert receipts[0]["published_object_count"] == 100
    assert 0 < receipts[0]["finalization_provider_bytes"] <= PROVIDER_HARD_LIMIT_BYTES


def test_v9_public_rebuild_paths_have_real_modes_and_stable_standard_identity(
    tmp_path: Path,
) -> None:
    _public_batch_smoke(tmp_path, batch_count=2, fragments_per_source=2)
    vault = tmp_path / "Vault"
    with AutonomousKnowledgeStore(vault, read_only=False) as store:
        full = store.rebuild_derived(projection_profile="standard")
    with AutonomousKnowledgeStore(vault, read_only=False) as store:
        minimal = store.rebuild_derived(projection_profile="minimal")
    with AutonomousKnowledgeStore(vault, read_only=False) as store:
        incremental = store.rebuild_derived(projection_profile="standard")
    with AutonomousKnowledgeStore(vault, read_only=False) as store:
        no_op = store.rebuild_derived(projection_profile="standard")
    assert sum(_change_counts(full)[field] for field in ("created", "updated", "deleted")) > 0
    assert sum(
        _change_counts(incremental)[field] for field in ("created", "updated", "deleted")
    ) > 0
    assert sum(_change_counts(no_op)[field] for field in ("created", "updated", "deleted")) == 0
    assert (
        _equivalence_digest(full)
        == _equivalence_digest(incremental)
        == _equivalence_digest(no_op)
    )
    assert _change_counts(minimal) != _change_counts(full)
