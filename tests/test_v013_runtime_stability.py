from __future__ import annotations

import json
import re
import subprocess
from pathlib import Path

import pytest
from jsonschema import Draft202012Validator, FormatChecker

import benchmarks.v013.runtime_stability as runtime_stability
from benchmarks.v013.runtime_stability import (
    FROZEN_REQUEST_COUNT,
    SCHEMA_VERSION,
    _LedgerCounts,
    _rss_result,
    _run_rss_child,
    build_report,
    verify_report,
)
from deeplaw import __version__

REPOSITORY = Path(__file__).resolve().parents[1]
SCHEMA_PATH = REPOSITORY / "contracts/v013-runtime-stability-report.v1.schema.json"
_LOCAL_PATH = re.compile(
    r"(?:/Users/|/home/|/tmp/|/private/var/|/var/folders/|[A-Za-z]:[\\/])"
)
def test_runtime_stability_smoke_is_schema_bound_and_read_only(tmp_path: Path) -> None:
    report = build_report(
        request_count=2,
        warmup_requests=1,
        workspace=tmp_path / "workspace",
    )
    schema = json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))
    Draft202012Validator.check_schema(schema)
    Draft202012Validator(schema, format_checker=FormatChecker()).validate(report)

    assert report["schema_version"] == SCHEMA_VERSION
    assert report["claim_eligible"] is False
    assert report["release_gate_passed"] is False
    assert report["candidate"]["package_version"] == __version__
    assert isinstance(report["candidate"]["working_tree_dirty"], bool)
    assert "src/deeplaw/knowledge_mcp_server.py" in report["candidate"]["source_hashes"]
    assert report["configuration"]["query_plan_version"] == "6"
    assert report["configuration"]["rss_growth_limit_percent"] == 10.0
    assert report["fixture"]["construction"] == "public_profile_v3_compilation"
    assert report["fixture"]["statement_count"] == 1, report["rss_stability"]["reason"]
    assert report["rss_stability"]["request_count"] == 2
    if runtime_stability._rss_method() is None:
        assert report["rss_stability"]["status"] == "not_executed"
        assert report["rss_stability"]["attempted_requests"] == 0
        assert report["rss_stability"]["successful_requests"] == 0
        assert report["rss_stability"]["reason"] == (
            "current RSS measurement is supported only on macOS and Linux"
        )
    else:
        assert report["rss_stability"]["attempted_requests"] == 2
        assert report["rss_stability"]["successful_requests"] == 2
    assert report["rss_stability"]["target_10k_executed"] is False
    assert report["rss_stability"]["growth_limit_percent"] == 10.0
    assert report["rss_stability"]["growth_limit_passed"] is None
    assert report["concurrent_readers"]["reader_count"] == 8
    assert report["concurrent_readers"]["successful_readers"] == 8
    assert report["concurrent_readers"]["receipt_consistent"] is True
    assert report["concurrent_readers"]["identity_consistent"] is True
    assert report["concurrent_readers"]["read_only_consistent"] is True
    assert report["canonical_ledger"]["unchanged"] is True
    assert report["rss_stability"]["canonical_ledger_unchanged"] is True
    assert report["concurrent_readers"]["canonical_ledger_unchanged"] is True
    assert verify_report(report) == {"valid": True, "errors": []}
    assert _LOCAL_PATH.search(json.dumps(report, ensure_ascii=False, sort_keys=True)) is None


def test_rss_child_uses_closed_portable_environment(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict[str, str] = {}

    def fake_run(*args: object, **kwargs: object) -> subprocess.CompletedProcess[str]:
        del args
        environment = kwargs["env"]
        assert isinstance(environment, dict)
        captured.update(environment)
        return subprocess.CompletedProcess(
            args=[],
            returncode=0,
            stdout='{"status":"not_executed"}\n',
            stderr="",
        )

    monkeypatch.setenv("DEEPLAW_TEST_AMBIENT_SECRET", "ambient-secret")
    monkeypatch.setenv("TEST_PROVIDER_TOKEN", "provider-secret")
    monkeypatch.setattr(runtime_stability.subprocess, "run", fake_run)

    result = _run_rss_child(
        tmp_path / "vault",
        request_count=2,
        warmup_requests=1,
    )

    assert result == {"status": "not_executed"}
    assert captured["HOME"] == str(tmp_path / ".runtime-child-home")
    assert captured["PYTHONNOUSERSITE"] == "1"
    assert captured["PYTHONUNBUFFERED"] == "1"
    assert "DEEPLAW_TEST_AMBIENT_SECRET" not in captured
    assert "TEST_PROVIDER_TOKEN" not in captured


def test_frozen_10k_never_runs_without_explicit_parameter(tmp_path: Path) -> None:
    report = build_report(
        request_count=FROZEN_REQUEST_COUNT,
        warmup_requests=0,
        workspace=tmp_path / "workspace",
    )
    assert report["configuration"]["request_count"] == FROZEN_REQUEST_COUNT
    assert report["rss_stability"]["status"] == "not_executed"
    assert report["rss_stability"]["attempted_requests"] == 0
    assert report["rss_stability"]["target_10k_executed"] is False
    assert "explicit" in str(report["rss_stability"]["reason"])
    assert report["release_gate_passed"] is False
    assert verify_report(report)["valid"] is True


@pytest.mark.parametrize(
    ("end", "expected"),
    ((109, True), (111, False)),
)
def test_frozen_rss_threshold_is_explicit(end: int, expected: bool) -> None:
    counts = _LedgerCounts(legacy_events=1, autonomous_events=1)
    result = _rss_result(
        status="executed",
        request_count=FROZEN_REQUEST_COUNT,
        warmup_requests=0,
        attempted=FROZEN_REQUEST_COUNT,
        successful=FROZEN_REQUEST_COUNT,
        failed=0,
        start=100,
        end=end,
        errors=(),
        ledger_before=counts,
        ledger_after=counts,
    )

    assert result["growth_limit_percent"] == 10.0
    assert result["growth_limit_passed"] is expected


def test_report_verifier_fails_closed_on_claim_or_path_tampering(tmp_path: Path) -> None:
    report = build_report(request_count=1, workspace=tmp_path / "workspace")

    claim_tampered = dict(report)
    claim_tampered["claim_eligible"] = True
    assert verify_report(claim_tampered)["valid"] is False

    path_tampered = dict(report)
    path_tampered["limitations"] = ["/Users/private/source.md"]
    assert verify_report(path_tampered)["valid"] is False


def test_fixture_failure_reason_is_bounded_enumerated_and_redacted(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def fail_fixture(_: Path) -> dict[str, object]:
        raise runtime_stability._RuntimeDiagnosticFailure("source_write", "os_error")

    monkeypatch.setattr(runtime_stability, "_build_fixture_report", fail_fixture)

    report = build_report(request_count=1)

    assert report["fixture"]["status"] == "fail"
    assert report["fixture"]["statement_count"] == 0
    assert report["rss_stability"]["reason"] == "fixture_failure:source_write:os_error"
    assert report["concurrent_readers"]["reason"] == report["rss_stability"]["reason"]
    serialized = json.dumps(report, ensure_ascii=False, sort_keys=True)
    assert "/Users/private/source.md" not in serialized
    assert "DEEPLAW_TEST_AMBIENT_SECRET" not in serialized
    assert verify_report(report) == {"valid": True, "errors": []}


def test_fixture_failure_verifier_rejects_unbounded_reason(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def fail_fixture(_: Path) -> dict[str, object]:
        raise OSError("/Users/private/source.md secret=do-not-record")

    monkeypatch.setattr(runtime_stability, "_build_fixture_report", fail_fixture)
    report = build_report(request_count=1)
    assert "source.md" not in json.dumps(report, ensure_ascii=False, sort_keys=True)

    report["rss_stability"]["reason"] = "raw /Users/private/source.md secret=do-not-record"

    assert verify_report(report)["valid"] is False


def test_invalid_request_arguments_fail_closed() -> None:
    with pytest.raises(ValueError):
        build_report(request_count=0)
    with pytest.raises(ValueError):
        build_report(request_count=FROZEN_REQUEST_COUNT + 1)
    with pytest.raises(ValueError):
        build_report(warmup_requests=-1)
