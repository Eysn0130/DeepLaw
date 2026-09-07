from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path

import pytest

from benchmarks.hosts.v013_task_service_observation import (
    SERVICE_SCHEMA_VERSION,
    TASK_RESULT_SCHEMA_VERSION,
    TaskServiceObservationError,
    _bounded_call,
    collect_task_service_observation,
    task_result_service_source,
)
from benchmarks.release.typed_qualification_evidence import (
    TypedQualificationEvidenceError,
    parse_typed_evidence,
)
from benchmarks.release.typed_qualification_evidence_v3_host_tasks import (
    HostTaskEvidenceError,
    _host_identity_projection,
)
from deeplaw.native_host import derive_native_host_receipt
from tests.test_v013_host_task_evidence import (
    _candidate,
    _event,
    _expected_sha,
    _manifest,
    _refresh_source_ref,
    _source,
)
from tests.test_v013_task_domain_driver import _prepared_vault


def _canonical(value: object) -> bytes:
    return json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode()


def _sha(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def _service_fixture(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> tuple[Path, Path, dict]:
    evidence_root = tmp_path / "evidence"
    evidence_root.mkdir()
    manifest = _manifest(evidence_root, host="codex", task="living_wiki", current=True)
    runtime_root = tmp_path / "runtime"
    runtime_root.mkdir()
    vault, seed = _prepared_vault(runtime_root, monkeypatch)
    observation = collect_task_service_observation(
        seed,
        vault=vault,
        native_event=_event("codex", "UserPromptSubmit", 1, current=True),
        run_id="v013:codex:living_wiki",
        workflow_run_id=13,
        candidate_binding=_candidate(),
        host="codex",
        task_case="living_wiki",
        event_index=1,
        deeplaw_executable=sys.executable,
        deeplaw_prefix=("-m", "deeplaw"),
    )
    service_path = evidence_root / "codex/living_wiki/service.json"
    service_path.parent.mkdir(parents=True, exist_ok=True)
    service_raw = _canonical(observation)
    service_path.write_bytes(service_raw)
    envelope = json.loads(manifest.read_text())
    result_path = evidence_root / envelope["payload"]["continuity_source"]["relative_path"]
    result = json.loads(result_path.read_text())
    result.update(
        {
            "schema_version": TASK_RESULT_SCHEMA_VERSION,
            "observed_public_seams": observation["observed_public_seams"],
            "service_source": {
                "relative_path": "codex/living_wiki/service.json",
                "byte_size": len(service_raw),
                "sha256": _sha(service_raw),
                "media_type": "application/json",
            },
        }
    )
    result_path.write_bytes(_canonical(result))
    _refresh_source_ref(manifest, "continuity_source", result_path)
    return manifest, service_path, observation


def test_current_native_v3_source_task_without_service_observation_fails(
    tmp_path: Path,
) -> None:
    manifest = _manifest(tmp_path, host="codex", task="living_wiki", current=True)
    result = parse_typed_evidence(
        manifest,
        root=manifest.parent,
        expected_corpus_sha256=_expected_sha(manifest.parent, "codex", "living_wiki"),
    )
    assert result["status"] == "failed"
    assert result["hard_failure_counts"]["missing_required_operation"] > 0


@pytest.mark.parametrize("downgrade", ["message_only", "all_events_v2_result_v2"])
def test_source_service_gate_rejects_native_schema_downgrade(
    tmp_path: Path, downgrade: str
) -> None:
    manifest = _manifest(tmp_path, host="codex", task="living_wiki")
    envelope = json.loads(manifest.read_text())
    event_path = tmp_path / envelope["payload"]["event_source"]["relative_path"]
    lifecycle_path = tmp_path / envelope["payload"]["lifecycle_source"]["relative_path"]
    events = json.loads(event_path.read_text())
    for event in events["events"]:
        if downgrade != "message_only" or event["event_type"] == "UserPromptSubmit":
            continue
        event["schema_version"] = "deeplaw.native-host-event/v3"
        event["execution_identity"] = {
            "selector_source_symlink": False,
            "execution_target_regular": True,
            "execution_target_single_link": True,
        }
        event["host_identity"] = _host_identity_projection(event["host_identity"], host="codex")
    event_path.write_bytes(_canonical(events))
    lifecycle = json.loads(lifecycle_path.read_text())
    lifecycle["receipts"] = [derive_native_host_receipt(event) for event in events["events"]]
    lifecycle_path.write_bytes(_canonical(lifecycle))
    _refresh_source_ref(manifest, "event_source", event_path)
    _refresh_source_ref(manifest, "lifecycle_source", lifecycle_path)
    if downgrade == "all_events_v2_result_v2":
        result_path = tmp_path / envelope["payload"]["continuity_source"]["relative_path"]
        result = json.loads(result_path.read_text())
        result.update(
            schema_version=TASK_RESULT_SCHEMA_VERSION,
            service_source=_source(tmp_path, "codex/living_wiki/service.json", {}),
        )
        result_path.write_bytes(_canonical(result))
        _refresh_source_ref(manifest, "continuity_source", result_path)
    with pytest.raises(TypedQualificationEvidenceError, match="requires only native-v3 events"):
        parse_typed_evidence(
            manifest,
            root=tmp_path,
            expected_corpus_sha256=_expected_sha(tmp_path, "codex", "living_wiki"),
        )


def test_task_result_service_source_helper_is_closed_and_compatible() -> None:
    assert task_result_service_source({"artifact_kind": "task_result"}) is None
    assert task_result_service_source(
        {
            "artifact_kind": "task_result",
            "schema_version": "deeplaw.v013-host-task-evidence/v1",
            "task_case": "living_wiki",
        }
    ) is None
    reference = {
        "relative_path": "codex/living_wiki/service.json",
        "byte_size": 1,
        "sha256": "a" * 64,
        "media_type": "application/json",
    }
    assert task_result_service_source(
        {
            "artifact_kind": "task_result",
            "schema_version": TASK_RESULT_SCHEMA_VERSION,
            "task_case": "living_wiki",
            "service_source": reference,
        }
    ) == reference
    with pytest.raises(TaskServiceObservationError):
        task_result_service_source(
            {
                "artifact_kind": "task_result",
                "schema_version": TASK_RESULT_SCHEMA_VERSION,
                "task_case": "living_wiki",
                "service_source": {**reference, "relative_path": "../service.json"},
            }
        )


@pytest.mark.parametrize(
    "field",
    [
        "event_index", "provider_bytes", "input_tokens", "output_tokens",
        "cache_tokens", "reasoning_tokens",
    ],
)
def test_task_result_rejects_boolean_numeric_evidence(tmp_path: Path, field: str) -> None:
    manifest = _manifest(tmp_path, host="codex", task="continuity")
    envelope = json.loads(manifest.read_text())
    result_path = tmp_path / envelope["payload"]["continuity_source"]["relative_path"]
    result = json.loads(result_path.read_text())
    if field == "event_index":
        result["first_correct_action"][field] = True
    else:
        result["provider"][field] = True
        usage_path = tmp_path / envelope["payload"]["usage_source"]["relative_path"]
        usage = json.loads(usage_path.read_text())
        assert len(usage["rows"]) == 1
        usage["rows"][0][field] = 1
        usage_path.write_bytes(_canonical(usage))
        _refresh_source_ref(manifest, "usage_source", usage_path)
    result_path.write_bytes(_canonical(result))
    _refresh_source_ref(manifest, "continuity_source", result_path)
    with pytest.raises(TypedQualificationEvidenceError, match="is invalid"):
        parse_typed_evidence(
            manifest, root=tmp_path,
            expected_corpus_sha256=_expected_sha(tmp_path, "codex", "continuity"),
        )


@pytest.mark.parametrize("part", ["request", "projection"])
@pytest.mark.parametrize("case", ["forged_small", "oversized", "oversized_forged"])
def test_bounded_call_rejects_forged_observed_bytes(part: str, case: str) -> None:
    # Malformed driver evidence, not an actual Host/MCP execution claim.
    size = 3 if case == "forged_small" else 30_000
    request = {"query": "x" * (size if part == "request" else 3), "limit": 1}
    projection = {
        "receipt_id": "x" * (size if part == "projection" else 3), "write_performed": False,
    }
    call = {
        "caller": "task_domain_driver", "operation": "knowledge_support",
        "action": "query", "response_kind": "bounded_projection",
        "request": request, "projection": projection,
    }
    for name, value in (("request", request), ("projection", projection)):
        raw = _canonical(value)
        call[f"{name}_byte_size"] = len(raw)
        call[f"{name}_sha256"] = _sha(raw)
    if case != "oversized":
        call[f"observed_{part}_byte_size"] = 1
        call[f"observed_{part}_sha256"] = "a" * 64
    with pytest.raises(TaskServiceObservationError):
        _bounded_call(call)


def test_actual_s075_service_projection_reopens_and_remains_partial(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    manifest, service_path, observation = _service_fixture(tmp_path, monkeypatch)
    assert observation["schema_version"] == SERVICE_SCHEMA_VERSION
    assert observation["caller"] == "task_domain_driver"
    assert observation["claim_eligible"] is False
    assert observation["observed_public_seams"]
    assert all("query" not in call["request"] for call in observation["service_calls"])
    assert all(
        "provider_content_bytes" not in call["projection"]
        for call in observation["service_calls"]
    )
    result = parse_typed_evidence(
        manifest,
        root=manifest.parent,
        expected_corpus_sha256=_expected_sha(manifest.parent, "codex", "living_wiki"),
    )
    assert result["status"] == "failed"
    assert result["hard_failure_counts"]["required_duty_gap"] > 0
    assert service_path.is_file()


def test_rehashed_wrong_source_identity_fails_closed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    manifest, service_path, _ = _service_fixture(tmp_path, monkeypatch)
    observation = json.loads(service_path.read_text())
    call = next(
        item
        for item in observation["service_calls"]
        if item["operation"] == "SourceReadService" and item["action"] == "get"
    )
    call["request"]["source_id"] = "source_ffffffffffffffffffffffff"
    request_raw = _canonical(call["request"])
    call["request_byte_size"] = len(request_raw)
    call["request_sha256"] = _sha(request_raw)
    call["observed_request_byte_size"] = len(request_raw)
    call["observed_request_sha256"] = _sha(request_raw)
    service_path.write_bytes(_canonical(observation))
    envelope = json.loads(manifest.read_text())
    result_path = manifest.parent / envelope["payload"]["continuity_source"]["relative_path"]
    result = json.loads(result_path.read_text())
    _refresh_source_ref(manifest, "continuity_source", result_path)
    result["service_source"]["byte_size"] = service_path.stat().st_size
    result["service_source"]["sha256"] = _sha(service_path.read_bytes())
    result_path.write_bytes(_canonical(result))
    _refresh_source_ref(manifest, "continuity_source", result_path)
    with pytest.raises((HostTaskEvidenceError, ValueError)):
        parse_typed_evidence(
            manifest,
            root=manifest.parent,
            expected_corpus_sha256=_expected_sha(manifest.parent, "codex", "living_wiki"),
        )


def test_v2_partial_duties_cannot_be_promoted_from_gap_rows(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    manifest, _service_path, observation = _service_fixture(tmp_path, monkeypatch)
    not_executed = set(observation["not_executed_duties"])
    envelope = json.loads(manifest.read_text())
    result_path = manifest.parent / envelope["payload"]["continuity_source"]["relative_path"]
    result = json.loads(result_path.read_text())
    result["duties"] = [
        {
            **row,
            "status": "not_executed" if row["duty"] in not_executed else row["status"],
            "gap_code": None if row["duty"] in not_executed else row["gap_code"],
        }
        for row in result["duties"]
    ]
    result_path.write_bytes(_canonical(result))
    _refresh_source_ref(manifest, "continuity_source", result_path)
    parsed = parse_typed_evidence(
        manifest,
        root=manifest.parent,
        expected_corpus_sha256=_expected_sha(manifest.parent, "codex", "living_wiki"),
    )
    assert parsed["status"] == "failed"
    assert parsed["hard_failure_counts"]["required_duty_gap"] >= len(not_executed)

    expected_ref = envelope["payload"]["expected_source"]
    expected_path = manifest.parent / expected_ref["relative_path"]
    expected = json.loads(expected_path.read_text())
    expected["duty_expectations"] = [
        {
            **row,
            "allowed_statuses": ["gap"] if duty in not_executed else row["allowed_statuses"],
            "required_gap_code": "driver_not_executed"
            if duty in not_executed
            else row["required_gap_code"],
        }
        for row in expected["duty_expectations"]
        for duty in [row["duty"]]
    ]
    result["duties"] = [
        {
            **row,
            "status": "gap" if row["duty"] in not_executed else row["status"],
            "gap_code": "driver_not_executed"
            if row["duty"] in not_executed
            else row["gap_code"],
        }
        for row in result["duties"]
    ]
    expected_path.write_bytes(_canonical(expected))
    _refresh_source_ref(manifest, "expected_source", expected_path)
    expected_sha = _sha(expected_path.read_bytes())
    manifest_data = json.loads(manifest.read_text())
    for source_key in ("usage_source", "isolation_source"):
        source_path = manifest.parent / manifest_data["payload"][source_key]["relative_path"]
        source_value = json.loads(source_path.read_text())
        if source_key == "usage_source":
            for row in source_value["rows"]:
                row["corpus_sha256"] = expected_sha
        else:
            source_value["corpus"]["sha256"] = expected_sha
        source_path.write_bytes(_canonical(source_value))
        _refresh_source_ref(manifest, source_key, source_path)
    manifest_data = json.loads(manifest.read_text())
    manifest_data["corpus"]["sha256"] = expected_sha
    manifest_data["record_sha256"] = _sha(
        _canonical({key: value for key, value in manifest_data.items() if key != "record_sha256"})
    )
    manifest.write_bytes(_canonical(manifest_data))
    result_path.write_bytes(_canonical(result))
    _refresh_source_ref(manifest, "continuity_source", result_path)
    parsed = parse_typed_evidence(
        manifest,
        root=manifest.parent,
        expected_corpus_sha256=expected_sha,
    )
    assert parsed["status"] == "failed"
    assert parsed["hard_failure_counts"]["required_duty_gap"] >= len(not_executed)


@pytest.mark.parametrize(
    "field",
    [
        "run_id",
        "candidate_binding",
        "session_sha256",
        "route",
        "host_identity_sha256",
        "workflow_run_id",
        "event_index",
    ],
)
def test_tampered_native_binding_fails_closed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, field: str
) -> None:
    manifest, service_path, _ = _service_fixture(tmp_path, monkeypatch)
    observation = json.loads(service_path.read_text())
    if field == "candidate_binding":
        observation["native_event_binding"]["candidate_binding"]["tree"] = "f" * 40
    elif field == "route":
        observation["native_event_binding"]["route"]["binding_sha256"] = "f" * 64
    elif field in {"workflow_run_id", "event_index"}:
        observation["native_event_binding"][field] = True
    else:
        observation["native_event_binding"][field] = (
            "f" * 64 if field != "run_id" else "wrong-run"
        )
    service_path.write_bytes(_canonical(observation))
    envelope = json.loads(manifest.read_text())
    result_path = manifest.parent / envelope["payload"]["continuity_source"]["relative_path"]
    result = json.loads(result_path.read_text())
    result["service_source"]["byte_size"] = service_path.stat().st_size
    result["service_source"]["sha256"] = _sha(service_path.read_bytes())
    result_path.write_bytes(_canonical(result))
    _refresh_source_ref(manifest, "continuity_source", result_path)
    with pytest.raises((HostTaskEvidenceError, ValueError)):
        parse_typed_evidence(
            manifest,
            root=manifest.parent,
            expected_corpus_sha256=_expected_sha(manifest.parent, "codex", "living_wiki"),
        )


def test_tampered_service_bytes_fail_before_parser(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    manifest, service_path, _ = _service_fixture(tmp_path, monkeypatch)
    service_path.write_bytes(b"{}")
    with pytest.raises(ValueError):
        parse_typed_evidence(
            manifest,
            root=manifest.parent,
            expected_corpus_sha256=_expected_sha(manifest.parent, "codex", "living_wiki"),
        )
