from __future__ import annotations

import sys

import pytest

from benchmarks.hosts.endpoint_security_observer import (
    ObservationUnavailable,
    build_and_probe,
    require_observation_capability,
    validate_probe,
)


def _unprivileged() -> dict[str, object]:
    return {
        "schema_version": "deeplaw.endpoint-security-capability/v1",
        "client_result": 5,
        "reason_code": "endpoint_security_root_required",
        "running_as_root": False,
        "descendants_api_available": False,
        "cleanup_confirmed": True,
        "event_subscription_started": False,
        "host_started": False,
        "formal_admission": False,
    }


def test_missing_native_authority_blocks_before_host_execution() -> None:
    result = _unprivileged()
    assert validate_probe(result) == result
    with pytest.raises(ObservationUnavailable, match="root_required"):
        require_observation_capability(result)
    assert result["host_started"] is False


@pytest.mark.parametrize(
    "change",
    [
        {"client_result": False},
        {"client_result": 0},
        {"formal_admission": True},
        {"host_started": True},
        {"event_subscription_started": True},
        {"cleanup_confirmed": False},
        {"descendants_api_available": 0},
    ],
)
def test_probe_cannot_promote_forged_or_partial_result(change: dict[str, object]) -> None:
    with pytest.raises(ObservationUnavailable):
        validate_probe({**_unprivileged(), **change})


def test_available_client_is_not_formal_process_or_isolation_evidence() -> None:
    result = {**_unprivileged(), "client_result": 0, "reason_code": "available"}
    require_observation_capability(result)
    assert result["formal_admission"] is False
    assert result["event_subscription_started"] is False


@pytest.mark.skipif(sys.platform != "darwin", reason="native macOS Endpoint Security probe")
def test_native_probe_reports_actual_access_without_subscribing(tmp_path) -> None:
    report = build_and_probe(tmp_path.resolve() / "probe")
    observation = validate_probe(report["observation"])
    assert observation["event_subscription_started"] is False
    assert observation["host_started"] is False
    assert observation["cleanup_confirmed"] is True
    assert report["formal_admission"] is False
    assert len(report["source_sha256"]) == len(report["binary_sha256"]) == 64
