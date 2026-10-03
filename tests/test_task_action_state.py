from __future__ import annotations

from copy import deepcopy

import pytest

from deeplaw.task_action_state import (
    EVIDENCE_LEVEL,
    SCHEMA_VERSION,
    action_resume_requirement,
    normalize_action_state,
    validate_action_transition,
)


def _state(
    status: str = "not_executed",
    *,
    action_id: str = "action_01",
    request_sha256: str = "a" * 64,
    outcome_sha256: str | None = None,
    expected_prior_run_id: str | None = None,
) -> dict[str, object]:
    return {
        "schema_version": SCHEMA_VERSION,
        "action_id": action_id,
        "request_sha256": request_sha256,
        "status": status,
        "outcome_sha256": outcome_sha256,
        "expected_prior_run_id": expected_prior_run_id,
        "evidence_level": EVIDENCE_LEVEL,
    }


@pytest.mark.parametrize(
    ("status", "outcome_sha256"),
    (
        ("not_executed", None),
        ("initiated_unknown", None),
        ("succeeded", "b" * 64),
        ("failed", "c" * 64),
    ),
)
def test_normalize_returns_exact_closed_shape_for_each_status(
    status: str, outcome_sha256: str | None
) -> None:
    value = _state(
        status,
        outcome_sha256=outcome_sha256,
        expected_prior_run_id=None if status == "not_executed" else "run_abc-01",
    )
    source = deepcopy(value)
    normalized = normalize_action_state(value)

    assert normalized == source
    assert tuple(normalized) == (
        "schema_version",
        "action_id",
        "request_sha256",
        "status",
        "outcome_sha256",
        "expected_prior_run_id",
        "evidence_level",
    )
    assert normalized is not value


@pytest.mark.parametrize(
    ("field", "value"),
    (
        ("schema_version", "deeplaw.task-action-state/v0"),
        ("action_id", "../escape"),
        ("action_id", "a/b"),
        ("action_id", ""),
        ("action_id", "x" * 65),
        ("request_sha256", "g" * 64),
        ("request_sha256", "a" * 63),
        ("status", "running"),
        ("outcome_sha256", "b" * 64),
        ("expected_prior_run_id", "../run"),
        ("expected_prior_run_id", ""),
        ("evidence_level", "model_reported"),
    ),
)
def test_normalize_rejects_invalid_fields(field: str, value: object) -> None:
    state = _state()
    state[field] = value
    with pytest.raises(ValueError):
        normalize_action_state(state)


def test_normalize_rejects_open_fields_and_terminal_missing_outcome() -> None:
    extra = _state()
    extra["extra"] = True
    with pytest.raises(ValueError, match="closed"):
        normalize_action_state(extra)

    missing = _state()
    del missing["request_sha256"]
    with pytest.raises(ValueError, match="closed"):
        normalize_action_state(missing)

    for status in ("succeeded", "failed"):
        with pytest.raises(ValueError, match="outcome"):
            normalize_action_state(_state(status, expected_prior_run_id="run_abc-01"))


def test_transition_requires_first_not_executed_and_then_prior_run() -> None:
    initial = _state()
    assert validate_action_transition(None, initial) == initial

    with pytest.raises(ValueError, match="first"):
        validate_action_transition(None, _state("initiated_unknown"))
    with pytest.raises(ValueError, match="first"):
        validate_action_transition(None, _state(expected_prior_run_id="run_abc-01"))

    unknown = _state("initiated_unknown", expected_prior_run_id="run_abc-01")
    assert validate_action_transition(initial, unknown) == unknown

    succeeded = _state(
        "succeeded",
        outcome_sha256="b" * 64,
        expected_prior_run_id="run_abc-01",
    )
    assert validate_action_transition(unknown, succeeded) == succeeded


@pytest.mark.parametrize(
    ("previous", "current"),
    (
        (
            _state(),
            _state(
                "succeeded",
                outcome_sha256="b" * 64,
                expected_prior_run_id="run_abc-01",
            ),
        ),
        (
            _state(),
            _state(
                "failed",
                outcome_sha256="c" * 64,
                expected_prior_run_id="run_abc-01",
            ),
        ),
        (_state("initiated_unknown", expected_prior_run_id="run_abc-01"), _state()),
        (
            _state("initiated_unknown", expected_prior_run_id="run_abc-01"),
            _state("initiated_unknown", expected_prior_run_id="run_abc-01"),
        ),
        (
            _state(
                "succeeded",
                outcome_sha256="b" * 64,
                expected_prior_run_id="run_abc-01",
            ),
            _state(
                "succeeded",
                outcome_sha256="b" * 64,
                expected_prior_run_id="run_abc-01",
            ),
        ),
        (
            _state(
                "failed",
                outcome_sha256="c" * 64,
                expected_prior_run_id="run_abc-01",
            ),
            _state("initiated_unknown", expected_prior_run_id="run_abc-01"),
        ),
    ),
)
def test_transition_rejects_invalid_status_progressions(
    previous: dict[str, object], current: dict[str, object]
) -> None:
    with pytest.raises(ValueError, match=r"transition|terminal|subsequent|prior"):
        validate_action_transition(previous, current)


@pytest.mark.parametrize("field", ("action_id", "request_sha256"))
def test_transition_rejects_changed_request_identity(field: str) -> None:
    previous = _state()
    current = _state("initiated_unknown", expected_prior_run_id="run_abc-01")
    current[field] = "different" if field == "action_id" else "d" * 64
    with pytest.raises(ValueError, match="identity"):
        validate_action_transition(previous, current)


def test_transition_points_to_the_immediately_preceding_run() -> None:
    previous = _state("initiated_unknown", expected_prior_run_id="run_abc-01")
    current = _state(
        "succeeded",
        outcome_sha256="b" * 64,
        expected_prior_run_id="run_def-02",
    )
    assert validate_action_transition(previous, current) == current


@pytest.mark.parametrize(
    ("status", "requirement"),
    (
        ("not_executed", "not_started"),
        ("initiated_unknown", "verify_external_state"),
        ("succeeded", "do_not_repeat"),
        ("failed", "review_failure"),
    ),
)
def test_action_resume_requirement_is_data_only(
    status: str, requirement: str
) -> None:
    state = _state(
        status,
        outcome_sha256="b" * 64 if status in {"succeeded", "failed"} else None,
        expected_prior_run_id=None if status == "not_executed" else "run_abc-01",
    )
    assert action_resume_requirement(state) == requirement
