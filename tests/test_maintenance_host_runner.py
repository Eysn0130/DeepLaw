from __future__ import annotations

import copy

import pytest

from benchmarks.hosts.run_maintenance_host_tasks import outcome_commit_eligible


def _completed() -> dict:
    return {"failure": None, "forced_kill": False, "host_exit_code": 0,
            "guard": {"cleanup_confirmed": True, "first_failure": None,
                      "cleanup_failure": None, "rejected": 0, "in_flight": 0}}


def test_completed_host_allows_outcome_commit() -> None:
    assert outcome_commit_eligible(_completed())


@pytest.mark.parametrize("field,value", [
    ("failure", "ModelIdentityError"), ("forced_kill", True),
    ("host_exit_code", 1), ("host_exit_code", None), ("host_exit_code", False),
])
def test_host_failure_prevents_success_commit(field: str, value: object) -> None:
    result = _completed()
    result[field] = value
    assert not outcome_commit_eligible(result)


@pytest.mark.parametrize("field,value", [
    ("cleanup_confirmed", False), ("first_failure", {"stage": "inspect"}),
    ("cleanup_failure", {"stage": "stop"}), ("rejected", 1), ("in_flight", 1),
])
def test_guard_failure_prevents_success_commit(field: str, value: object) -> None:
    result = copy.deepcopy(_completed())
    result["guard"][field] = value
    assert not outcome_commit_eligible(result)
