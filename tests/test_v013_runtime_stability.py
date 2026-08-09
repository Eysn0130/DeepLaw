from __future__ import annotations

import json
import re
from pathlib import Path

import pytest
from jsonschema import Draft202012Validator, FormatChecker

import benchmarks.v013.runtime_stability as runtime_stability
from benchmarks.v013.runtime_stability import (
    FROZEN_REQUEST_COUNT,
    SCHEMA_VERSION,
    _LedgerCounts,
    _rss_result,
    build_report,
    verify_report,
)

REPOSITORY = Path(__file__).resolve().parents[1]
SCHEMA_PATH = REPOSITORY / "contracts/v013-runtime-stability-report.v1.schema.json"
_LOCAL_PATH = re.compile(
    r"(?:/Users/|/home/|/tmp/|/private/var/|/var/folders/|[A-Za-z]:[\\/])"
)
_FIXTURE_DIAGNOSTIC_MESSAGES = {
    "source compilation artifact metadata is inconsistent": "artifact_metadata_mismatch",
    "content-addressed object path is unsafe": "object_path_unsafe",
    "content-addressed object failed exact-byte verification": "object_byte_mismatch",
    "autonomous event transaction time moved backwards": "transaction_time_regression",
}
_FIXTURE_DIAGNOSTIC_FRAMES = {
    "_artifact": "artifact",
    "_write_object": "write_object",
    "_atomic_owner_write": "atomic_write",
    "_owner_directory": "owner_directory",
    "_next_transaction_time": "transaction_time",
    "_commit_statement_fixture": "fixture_commit",
}


def _closed_fixture_diagnostic(workspace: Path) -> str:
    try:
        runtime_stability._build_fixture_report(workspace)
    except BaseException as error:
        seen: set[int] = set()
        current: BaseException | None = error
        frames: list[str] = []
        while current is not None and id(current) not in seen:
            seen.add(id(current))
            code = _FIXTURE_DIAGNOSTIC_MESSAGES.get(str(current))
            if code is not None:
                return code
            traceback = current.__traceback__
            while traceback is not None:
                frame = _FIXTURE_DIAGNOSTIC_FRAMES.get(
                    traceback.tb_frame.f_code.co_name
                )
                if frame is not None and frame not in frames:
                    frames.append(frame)
                traceback = traceback.tb_next
            current = current.__cause__ or current.__context__
        suffix = "_".join(frames) if frames else "no_known_frame"
        return f"unmapped_{runtime_stability._failure_type(error)}_{suffix}"
    return "not_reproduced"


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
    assert report["candidate"]["package_version"] == "0.12.0"
    assert isinstance(report["candidate"]["working_tree_dirty"], bool)
    assert "src/deeplaw/knowledge_mcp_server.py" in report["candidate"]["source_hashes"]
    assert report["configuration"]["query_plan_version"] == "6"
    assert report["configuration"]["rss_growth_limit_percent"] == 10.0
    assert report["fixture"]["construction"] == "public_profile_v3_compilation"
    assert report["fixture"]["statement_count"] == 1, (
        report["rss_stability"]["reason"],
        _closed_fixture_diagnostic(tmp_path / "diagnostic-workspace"),
    )
    assert report["rss_stability"]["request_count"] == 2
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
