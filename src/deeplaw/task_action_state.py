"""Pure validation for resumable task action state.

The state is an observation envelope for a single action request.  It does not
execute an action, persist state, perform a Run compare-and-set, or establish
independent verification of a Host or model report.  A caller that needs those
properties must obtain them from the owning Host, Run, and Sink boundaries.
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from typing import Any

SCHEMA_VERSION = "deeplaw.task-action-state/v1"
EVIDENCE_LEVEL = "host_reported"

_ACTION_ID = re.compile(r"^[A-Za-z0-9_-]{1,64}$")
_RUN_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,199}$")
_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_FIELDS = (
    "schema_version",
    "action_id",
    "request_sha256",
    "status",
    "outcome_sha256",
    "expected_prior_run_id",
    "evidence_level",
)
_FIELD_SET = frozenset(_FIELDS)
_STATUSES = frozenset(
    {"not_executed", "initiated_unknown", "succeeded", "failed"}
)
_UNSET_OUTCOME_STATUSES = frozenset({"not_executed", "initiated_unknown"})
_TERMINAL_STATUSES = frozenset({"succeeded", "failed"})
_NEXT_STATUS = {
    "not_executed": "initiated_unknown",
    "initiated_unknown": _TERMINAL_STATUSES,
}


def _is_sha256(value: Any) -> bool:
    return isinstance(value, str) and _SHA256.fullmatch(value) is not None


def _normalize_expected_prior_run_id(value: Any) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str) or _RUN_ID.fullmatch(value) is None:
        raise ValueError("expected_prior_run_id is invalid")
    return value


def normalize_action_state(value: Mapping[str, Any]) -> dict[str, Any]:
    """Validate and return a fresh, closed task action state dictionary.

    ``host_reported`` identifies the evidence channel only.  It does not mean
    that a model self-report is independently verified or that a Run CAS has
    succeeded.
    """

    if not isinstance(value, Mapping):
        raise ValueError("task action state must be an object")
    if set(value) != _FIELD_SET:
        raise ValueError("task action state keys are not closed")
    if value["schema_version"] != SCHEMA_VERSION:
        raise ValueError("task action state schema is unsupported")

    action_id = value["action_id"]
    if not isinstance(action_id, str) or _ACTION_ID.fullmatch(action_id) is None:
        raise ValueError("action_id is invalid")

    request_sha256 = value["request_sha256"]
    if not _is_sha256(request_sha256):
        raise ValueError("request_sha256 is invalid")

    status = value["status"]
    if not isinstance(status, str) or status not in _STATUSES:
        raise ValueError("task action status is invalid")

    outcome_sha256 = value["outcome_sha256"]
    if status in _UNSET_OUTCOME_STATUSES:
        if outcome_sha256 is not None:
            raise ValueError("outcome_sha256 must be null before a terminal outcome")
    elif not _is_sha256(outcome_sha256):
        raise ValueError("outcome_sha256 is required for a terminal outcome")

    expected_prior_run_id = _normalize_expected_prior_run_id(
        value["expected_prior_run_id"]
    )
    if value["evidence_level"] != EVIDENCE_LEVEL:
        raise ValueError("task action evidence level is unsupported")

    return {
        "schema_version": SCHEMA_VERSION,
        "action_id": action_id,
        "request_sha256": request_sha256,
        "status": status,
        "outcome_sha256": outcome_sha256,
        "expected_prior_run_id": expected_prior_run_id,
        "evidence_level": EVIDENCE_LEVEL,
    }


def validate_action_transition(
    previous: Mapping[str, Any] | None,
    current: Mapping[str, Any],
) -> dict[str, Any]:
    """Validate one state transition and return normalized ``current``.

    The first state is an explicit ``not_executed`` record with no prior Run.
    Any later state must name a non-empty expected prior Run identity.  The
    owning integration performs the actual Run CAS; this pure function only
    checks the state shape, stable request identity, and allowed progression.
    Sink idempotency handles replay of the same request separately, so this
    validator does not treat repeated states as valid transitions.
    """

    normalized_current = normalize_action_state(current)
    if previous is None:
        if (
            normalized_current["status"] != "not_executed"
            or normalized_current["expected_prior_run_id"] is not None
        ):
            raise ValueError(
                "the first task action state must be not_executed with no prior Run"
            )
        return normalized_current

    normalized_previous = normalize_action_state(previous)
    if normalized_previous["action_id"] != normalized_current["action_id"]:
        raise ValueError("task action identity changed")
    if normalized_previous["request_sha256"] != normalized_current["request_sha256"]:
        raise ValueError("task action request identity changed")
    if normalized_previous["status"] in _TERMINAL_STATUSES:
        raise ValueError("terminal task action state cannot transition")
    previous_status = normalized_previous["status"]
    previous_prior_run_id = normalized_previous["expected_prior_run_id"]
    if (
        previous_status == "not_executed" and previous_prior_run_id is not None
    ) or (previous_status != "not_executed" and previous_prior_run_id is None):
        raise ValueError("previous task action state has an invalid prior Run binding")
    if normalized_current["expected_prior_run_id"] is None:
        raise ValueError("subsequent task action state requires a prior Run")

    allowed_next = _NEXT_STATUS.get(previous_status)
    if isinstance(allowed_next, str):
        is_allowed = normalized_current["status"] == allowed_next
    else:
        is_allowed = normalized_current["status"] in allowed_next
    if not is_allowed:
        raise ValueError("task action status transition is invalid")
    return normalized_current


def action_resume_requirement(state: Mapping[str, Any]) -> str:
    """Return a data-only resume suggestion for a normalized action state."""

    status = normalize_action_state(state)["status"]
    return {
        "not_executed": "not_started",
        "initiated_unknown": "verify_external_state",
        "succeeded": "do_not_repeat",
        "failed": "review_failure",
    }[status]


__all__ = [
    "EVIDENCE_LEVEL",
    "SCHEMA_VERSION",
    "action_resume_requirement",
    "normalize_action_state",
    "validate_action_transition",
]
