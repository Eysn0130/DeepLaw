"""Focused tests for the exact-candidate commercial Gate v9 assembler."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
from jsonschema import Draft202012Validator, FormatChecker

from benchmarks.release import assemble_commercial_qualification_v9 as assembler
from benchmarks.release import kernel_qualification_bundle_v1 as bundle
from tests.test_v013_kernel_qualification_bundle_v1 import (
    EXPECTED_CANDIDATE,
    RUN_IDS,
    _make_fixture,
    _write_json,
)


def _prepare_bundle(root: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    root.mkdir()
    external_identity = _make_fixture(root, monkeypatch)
    for path in sorted((root / "typed").glob("host_event_sequence-*.json")):
        value = json.loads(path.read_text(encoding="utf-8"))
        host = "codex" if "codex" in path.name else "opencode"
        index = int(path.stem.rsplit("-", 1)[-1])
        value["run_binding"]["run_id"] = f"fixture-{host}-{index}"
        value["record_sha256"] = bundle.record_sha256(value)
        _write_json(path, value)
    bundle.build_bundle(
        root,
        run_ids=RUN_IDS,
        expected_candidate=EXPECTED_CANDIDATE,
        host_identity_input=external_identity,
    )
    return root


def _install_parser(
    monkeypatch: pytest.MonkeyPatch,
    *,
    failed_kind: str | None = None,
    events: list[str] | None = None,
    omit_failure_id: str | None = None,
    omit_metric: str | None = None,
    metric_overrides: dict[str, float] | None = None,
) -> None:
    classification = json.loads(
        (Path(__file__).parents[1] / assembler.CLASSIFICATION_RELATIVE_PATH).read_text(
            encoding="utf-8"
        )
    )
    metrics_by_kind: dict[str, dict[str, Any]] = {}
    failures_by_kind: dict[str, dict[str, int]] = {}
    for gate in classification["gates"]:
        if gate["category"] != "Core":
            continue
        kind = gate["artifact_kinds"][0]
        metrics = metrics_by_kind.setdefault(kind, {})
        failures = failures_by_kind.setdefault(kind, {})
        for threshold in gate["thresholds"]:
            metrics[threshold["metric"]] = (
                threshold["minimum"]
                if threshold["minimum"] is not None
                else (
                    threshold["maximum"]
                    if threshold["maximum"] is not None
                    else 1.0
                )
            )
        for failure_id in gate["hard_zero_derivation"]["failure_ids"]:
            failures[failure_id] = 0

    def fake_parse(path: Path, **_: Any) -> dict[str, Any]:
        if events is not None:
            events.append("parser")
        value = json.loads(path.read_text(encoding="utf-8"))
        kind = value["kind"]
        metrics: dict[str, Any] = {"fixture_metric": 1, **metrics_by_kind[kind]}
        if metric_overrides:
            metrics.update(metric_overrides)
        if kind == "host_event_sequence":
            host = "codex" if "codex" in path.as_posix() else "opencode"
            index = int(path.stem.rsplit("-", 1)[-1])
            metrics.update(
                host=host,
                task_case=("continuity", "living_wiki", "professional_evidence")[
                    index % 3
                ],
                run_id=f"fixture-{host}-{index}",
            )
        failures = dict(failures_by_kind[kind])
        if omit_failure_id is not None:
            failures.pop(omit_failure_id, None)
        if omit_metric is not None:
            metrics.pop(omit_metric, None)
        failed = kind == failed_kind
        if failed:
            failures[next(iter(failures))] = 1
        return {
            "schema_version": assembler.TYPED_DERIVED_SCHEMA_VERSION,
            "kind": kind,
            "status": "failed" if failed else "passed",
            "evidence_record_sha256": value["record_sha256"],
            "metrics": metrics,
            "hard_failure_counts": failures,
        }

    monkeypatch.setattr(assembler, "parse_typed_evidence", fake_parse)


def _schema(name: str) -> dict[str, Any]:
    path = Path(__file__).parents[1] / "contracts" / name
    return json.loads(path.read_text(encoding="utf-8"))


def _assert_schema(value: dict[str, Any], name: str) -> None:
    errors = list(
        Draft202012Validator(
            _schema(name), format_checker=FormatChecker()
        ).iter_errors(value)
    )
    assert errors == []


def test_assembles_exact_27_gates_and_bound_report(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    events: list[str] = []
    root = _prepare_bundle(tmp_path / "bundle", monkeypatch)
    original_validate = bundle.validate_bundle

    def validate_first(path: Path, **kwargs: Any) -> dict[str, Any]:
        events.append("bundle")
        return original_validate(path, **kwargs)

    monkeypatch.setattr(bundle, "validate_bundle", validate_first)
    _install_parser(monkeypatch, events=events)
    output = tmp_path / "output"

    result = assembler.assemble_commercial_qualification(
        bundle_root=root,
        output_root=output,
    )

    assert events[0] == "bundle"
    assert result["gate_count"] == 27
    assert result["core_gate_count"] == 13
    assert result["optional_gate_count"] == 14
    assert result["status"] == "passed"
    assert result["release_ready"] is True

    report_path = output / result["report_path"]
    report = json.loads(report_path.read_text(encoding="utf-8"))
    _assert_schema(report, "commercial-evidence-report.v6.schema.json")
    assert report["kernel_release_core_passed"] is True
    assert report["release_ready"] is True
    assert report["human_attested_claim_eligible"] is False
    assert report["competitive_claim_eligible"] is False
    assert len(report["gate_results"]) == 27

    categories = {item["category"] for item in report["gate_results"]}
    assert categories == {"Core", "Capability", "Competitive/Research Claim"}
    for reference in report["gate_results"]:
        artifact = reference["result"]
        relative = Path(artifact["relative_path"])
        assert not relative.is_absolute()
        raw = (output / relative).read_bytes()
        value = json.loads(raw)
        _assert_schema(value, "provenance-bound-gate-result.v5.schema.json")
        assert artifact["byte_size"] == len(raw)
        assert artifact["file_sha256"] == assembler._sha256(raw)
        assert artifact["record_sha256"] == value["result_sha256"]
        if reference["category"] == "Core":
            assert value["status"] == "passed"
            assert value["executions"]
            assert value["run_ids"]
            assert value["metrics"]
            assert value["hard_failures"]
            assert value["inputs"]
        else:
            assert value["status"] == "not_executed"
            assert value["executions"] == []
            assert value["run_ids"] == []
            assert value["metrics"] == []
            assert value["hard_failures"] == []
            assert value["inputs"] == []


def test_core_failure_never_makes_report_release_ready(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    root = _prepare_bundle(tmp_path / "bundle", monkeypatch)
    _install_parser(
        monkeypatch,
        metric_overrides={"p95": 2_001.0},
    )

    result = assembler.assemble_commercial_qualification(
        bundle_root=root,
        output_root=tmp_path / "output",
    )

    assert result["status"] == "failed"
    assert result["kernel_release_core_passed"] is False
    assert result["release_ready"] is False
    report = json.loads(
        (tmp_path / "output" / result["report_path"]).read_text(encoding="utf-8")
    )
    failed = {
        item["gate_id"]
        for item in report["gate_results"]
        if item["category"] == "Core"
        and json.loads(
            (tmp_path / "output" / item["result"]["relative_path"]).read_text(
                encoding="utf-8"
            )
        )["status"]
        == "failed"
    }
    assert failed == {"scale_performance"}


def test_missing_classified_failure_or_threshold_metric_is_rejected(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    root = _prepare_bundle(tmp_path / "failure-bundle", monkeypatch)
    _install_parser(monkeypatch, omit_failure_id="provider_bound_exceeded")
    with pytest.raises(
        assembler.CommercialQualificationAssemblerError,
        match="hard-failure coverage",
    ):
        assembler.assemble_commercial_qualification(
            bundle_root=root,
            output_root=tmp_path / "failure-output",
        )

    root = _prepare_bundle(tmp_path / "metric-bundle", monkeypatch)
    _install_parser(monkeypatch, omit_metric="wiki_journey_pass_rate")
    with pytest.raises(
        assembler.CommercialQualificationAssemblerError,
        match="threshold metric",
    ):
        assembler.assemble_commercial_qualification(
            bundle_root=root,
            output_root=tmp_path / "metric-output",
        )


def test_competitive_input_is_rejected_before_assembly(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    root = _prepare_bundle(tmp_path / "bundle", monkeypatch)
    manifest_path = root / bundle.BUNDLE_MANIFEST_NAME
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["scorer_panel"] = {"identity": "fixture"}
    manifest["record_sha256"] = bundle.record_sha256(manifest)
    _write_json(manifest_path, manifest)

    with pytest.raises(assembler.CommercialQualificationAssemblerError):
        assembler.assemble_commercial_qualification(
            bundle_root=root,
            output_root=tmp_path / "output",
        )


def test_tamper_and_candidate_mismatch_fail_closed(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    root = _prepare_bundle(tmp_path / "tampered-bundle", monkeypatch)
    typed_path = root / "typed" / "scale_report.json"
    typed_path.write_bytes(typed_path.read_bytes() + b" ")
    with pytest.raises(assembler.CommercialQualificationAssemblerError):
        assembler.assemble_commercial_qualification(
            bundle_root=root,
            output_root=tmp_path / "tampered-output",
        )

    root = _prepare_bundle(tmp_path / "candidate-bundle", monkeypatch)
    mismatched = dict(EXPECTED_CANDIDATE)
    mismatched["commit"] = "f" * 40
    with pytest.raises(assembler.CommercialQualificationAssemblerError):
        assembler.assemble_commercial_qualification(
            bundle_root=root,
            output_root=tmp_path / "candidate-output",
            expected_candidate=mismatched,
        )
