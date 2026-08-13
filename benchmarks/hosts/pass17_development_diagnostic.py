"""Source-free development fixture for claim-ineligible native Host diagnostics."""

from __future__ import annotations

import json
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from jsonschema import Draft202012Validator

_FIXTURE = Path(__file__).with_name("pass17-development-diagnostic-v1.json")
_SCHEMA = Path(__file__).resolve().parents[2] / "contracts" / (
    "host-continuity-development-diagnostic.v1.schema.json"
)
_FORBIDDEN_LABELS = ("human gold", "qualification_holdout", "blind_score", "expected_score")


class DevelopmentDiagnosticFixtureError(ValueError):
    """The source-free development fixture is missing or unsafe."""


def load_fixture() -> dict[str, Any]:
    try:
        value = json.loads(_FIXTURE.read_text(encoding="utf-8"))
        schema = json.loads(_SCHEMA.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, ValueError) as exc:
        raise DevelopmentDiagnosticFixtureError(
            "development diagnostic fixture is unavailable"
        ) from exc
    if not isinstance(value, Mapping):
        raise DevelopmentDiagnosticFixtureError("development diagnostic fixture is invalid")
    errors = sorted(
        Draft202012Validator(schema).iter_errors(value),
        key=lambda error: tuple(error.absolute_path),
    )
    encoded = json.dumps(value, ensure_ascii=False, sort_keys=True).casefold()
    if errors or any(label in encoded for label in _FORBIDDEN_LABELS):
        raise DevelopmentDiagnosticFixtureError(
            "development diagnostic fixture contains evaluator material"
        )
    return json.loads(json.dumps(value, ensure_ascii=False))


def candidate_prompt(fixture: Mapping[str, Any]) -> str:
    prompt = fixture.get("task_prompt")
    if not isinstance(prompt, str) or not prompt.strip():
        raise DevelopmentDiagnosticFixtureError("development diagnostic prompt is missing")
    return (
        f"{prompt.strip()} Use exactly one safe read-only knowledge_support context call; "
        "retry at most once only when the first bounded Provider Capsule is insufficient. "
        "Set confirm_no_case_data=true and do not invoke any other tool. Return only the "
        "bounded JSON response required by the Host."
    )
