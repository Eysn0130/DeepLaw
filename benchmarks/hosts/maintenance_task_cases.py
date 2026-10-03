"""Bounded, host-submitted maintenance task cases.

The cases in this module are deliberately synthetic.  A caller receives a
public task projection and submits actions to :class:`MaintenanceTaskSession`.
The session is a small state machine; it never chooses an action or executes a
provider/model.  Private case data is used only by ``score_host_trace`` after
the host has returned its trace.
"""

from __future__ import annotations

import copy
import hashlib
import json
import re
from collections.abc import Iterator, Mapping, Sequence
from typing import Any

SCHEMA_VERSION = "deeplaw.host-maintenance-task/v1"
EVENT_SCHEMA_VERSION = "deeplaw.host-maintenance-event/v1"
SCORE_SCHEMA_VERSION = "deeplaw.host-maintenance-score/v1"

CONFIGURATION_ORDER = (
    "no_memory",
    "frozen_unmaintained",
    "governed_maintenance",
)
SCENARIO_ORDER = (
    "source_update",
    "wrong_experience",
    "independent_support",
    "unknown_action",
    "forget_then_reuse",
)
# Keep the runner vocabulary available to callers that treat each scenario as
# a benchmark case.
CASE_ORDER = SCENARIO_ORDER

# Every public task receives the same action and event budget.  The values are
# intentionally small enough that a real host cannot turn this fixture into a
# general purpose executor.
ACTION_BUDGET = {
    "max_actions": 3,
    "max_action_bytes": 4096,
    "max_event_bytes": 8192,
}

_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_ZERO_SHA256 = "0" * 64
_RESOURCE_ID = "orchid-archive"
_REPORT_ID = "amber-report"
_SOURCE_V1 = "source:orchid-v1"
_SOURCE_V2 = "source:orchid-v2"
_SOURCE_CONTENT = "Orchid archive policy requires amber labels."
_SUPPORT_REFS = ("support:archive-alpha", "support:archive-beta")
_CURRENT_EXPERIENCE = "experience:governed-v2"
_FORGOTTEN_EXPERIENCE = "experience:forgotten-shortcut"

_ACTION_KINDS = frozenset(
    {
        "submit_resource_version",
        "approve_report",
        "withdraw_support",
        "record_unknown",
        "record_report_note",
    }
)
_EXPIRED_RESULT_CODES = frozenset(
    {
        "stale_state",
        "stale_source_reference",
        "stale_experience",
        "forgotten_experience_reuse",
        "withdrawn_support_reference",
    }
)
_CONSTRAINT_RESULT_CODES = frozenset(
    {
        "constraint_violation",
        "resource_identity_mismatch",
        "resource_version_unavailable",
        "resource_already_current",
        "report_identity_mismatch",
        "invalid_report_status",
        "report_already_approved",
        "support_incomplete",
        "support_duplicate",
        "support_unknown",
        "support_excess",
        "support_already_withdrawn",
        "unknown_experience",
        "inapplicable_experience",
        "wrong_experience",
        "invalid_note",
    }
)
_EVENT_KEYS = frozenset(
    {
        "schema_version",
        "event_ordinal",
        "action_id",
        "kind",
        "parameters",
        "observed_state_sha256",
        "status",
        "result_code",
        "retry_allowed",
        "state_mutated",
        "damage_class",
        "typed_result",
        "state_revision",
        "state_sha256",
        "state",
        "previous_event_sha256",
        "event_sha256",
    }
)


class MaintenanceCaseError(ValueError):
    """Raised when a requested configuration or scenario is not frozen."""


class MaintenanceActionError(ValueError):
    """Raised for malformed or over-budget host action envelopes."""


def _canonical(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _sha256_json(value: Any) -> str:
    return hashlib.sha256(_canonical(value).encode("utf-8")).hexdigest()


def _copy(value: Any) -> Any:
    return copy.deepcopy(value)


def _case_key(configuration_id: str, scenario_id: str) -> tuple[str, str]:
    if configuration_id not in CONFIGURATION_ORDER:
        raise MaintenanceCaseError(f"unknown configuration: {configuration_id}")
    if scenario_id not in SCENARIO_ORDER:
        raise MaintenanceCaseError(f"unknown scenario: {scenario_id}")
    return configuration_id, scenario_id


def _source_revision(version: str, source_ref: str, status: str) -> dict[str, Any]:
    return {
        "version": version,
        "source_ref": source_ref,
        "content": _SOURCE_CONTENT,
        "content_sha256": _sha256_json(_SOURCE_CONTENT),
        "status": status,
    }


def _resource_state(scenario_id: str) -> dict[str, Any]:
    updating = scenario_id == "source_update"
    current_version = "v1" if updating else "v2"
    current_source_ref = _SOURCE_V1 if updating else _SOURCE_V2
    revisions = [
        _source_revision(
            "v1",
            _SOURCE_V1,
            "current" if updating else "superseded",
        ),
        _source_revision(
            "v2",
            _SOURCE_V2,
            "available" if updating else "current",
        ),
    ]
    return {
        "resource_id": _RESOURCE_ID,
        "version": current_version,
        "source_ref": current_source_ref,
        "content": _SOURCE_CONTENT,
        "content_sha256": _sha256_json(_SOURCE_CONTENT),
        "available_versions": ["v1", "v2"] if updating else ["v2"],
        "available_source_ref": _SOURCE_V2,
        "source_revisions": revisions,
    }


def _report_state(scenario_id: str) -> dict[str, Any]:
    review_basis = {
        "wrong_experience": "current_source_and_archive_records",
        "independent_support": "independent_archive_records",
        "forget_then_reuse": "current_records_after_experience_removal",
    }.get(scenario_id, "source_revision_update")
    return {
        "report_id": _REPORT_ID,
        "title": "Amber label review",
        "claim": _SOURCE_CONTENT,
        "status": "pending",
        "review_basis": review_basis,
    }


def _support_records() -> list[dict[str, Any]]:
    records = []
    for support_id, label in zip(_SUPPORT_REFS, ("Alpha", "Beta"), strict=True):
        content = f"Independent {label} archive record confirms: {_SOURCE_CONTENT}"
        records.append(
            {
                "support_id": support_id,
                "source_ref": f"source:{support_id.removeprefix('support:')}",
                "content": content,
                "content_sha256": _sha256_json(content),
                "status": "available",
                "completeness": "complete",
                "independence": "independent",
            }
        )
    return records


def _experience_records(configuration_id: str) -> dict[str, dict[str, Any]]:
    if configuration_id == "no_memory":
        return {}
    if configuration_id == "frozen_unmaintained":
        return {
            "experience:source-v1": {
                "lifecycle": "active",
                "truth": "obsolete",
                "applies_to": ["source_update"],
                "content": "Submit Orchid archive source v1 as the current revision.",
                "content_sha256": _sha256_json(
                    "Submit Orchid archive source v1 as the current revision."
                ),
                "source_ref": _SOURCE_V1,
                "governance_status": "unmaintained",
                "freshness": "stale",
            },
            "experience:shortcut-v1": {
                "lifecycle": "active",
                "truth": "wrong",
                "applies_to": ["wrong_experience"],
                "content": (
                    "Approve the Amber report because the v1 archive is final; "
                    "no independent record is needed."
                ),
                "content_sha256": _sha256_json(
                    "Approve the Amber report because the v1 archive is final; "
                    "no independent record is needed."
                ),
                "source_ref": _SOURCE_V1,
                "governance_status": "unmaintained",
                "freshness": "stale",
            },
            _FORGOTTEN_EXPERIENCE: {
                "lifecycle": "forgotten",
                "truth": "obsolete",
                "applies_to": [],
                "content": "Use the old shortcut without checking current records.",
                "content_sha256": _sha256_json(
                    "Use the old shortcut without checking current records."
                ),
                "source_ref": _SOURCE_V1,
                "governance_status": "forgotten",
                "freshness": "unavailable",
            },
        }
    return {
        _CURRENT_EXPERIENCE: {
            "lifecycle": "active",
            "truth": "current",
            "applies_to": ["source_update"],
            "content": f"Governed v2 source record: {_SOURCE_CONTENT}",
            "content_sha256": _sha256_json(f"Governed v2 source record: {_SOURCE_CONTENT}"),
            "source_ref": _SOURCE_V2,
            "governance_status": "governed",
            "freshness": "current",
        },
        _FORGOTTEN_EXPERIENCE: {
            "lifecycle": "forgotten",
            "truth": "obsolete",
            "applies_to": [],
            "content": "Use the old shortcut without checking current records.",
            "content_sha256": _sha256_json(
                "Use the old shortcut without checking current records."
            ),
            "source_ref": _SOURCE_V1,
            "governance_status": "forgotten",
            "freshness": "unavailable",
        },
    }


def _task_text(scenario_id: str) -> tuple[str, str]:
    values = {
        "source_update": (
            "Inspect the Orchid archive source revisions and the current resource state.",
            "Leave the resource aligned with the available source revision.",
        ),
        "wrong_experience": (
            "Inspect the Amber report against the current Orchid source and archive records.",
            "Resolve conflicting retained experience from the publicly verifiable records.",
        ),
        "independent_support": (
            "Withdraw one independent archive record, then approve the Amber report using "
            "the remaining record.",
            "Leave one record withdrawn and the report supported by a complete independent "
            "record that remains available.",
        ),
        "unknown_action": (
            "Handle the legacy_export archive operation whose execution outcome is unknown.",
            "Record the unknown outcome for legacy_export and stop without changing "
            "the public resource or report.",
        ),
        "forget_then_reuse": (
            "Review the Amber report after an earlier archive experience was forgotten.",
            "Use current public evidence while preserving the forgotten item's boundary.",
        ),
    }
    return values[scenario_id]


def _result(
    status: str,
    result_code: str,
    *,
    state_mutated: bool,
    damage_class: str = "none",
) -> dict[str, Any]:
    return {
        "status": status,
        "result_code": result_code,
        "retry_allowed": False,
        "state_mutated": state_mutated,
        "damage_class": damage_class,
    }


def _expected_sequences(
    configuration_id: str, scenario_id: str
) -> list[list[dict[str, Any]]]:
    if scenario_id == "source_update":
        parameters: dict[str, Any] = {
            "resource_id": _RESOURCE_ID,
            "version": "v2",
            "source_ref": _SOURCE_V2,
        }
        if configuration_id == "governed_maintenance":
            parameters["experience_id"] = _CURRENT_EXPERIENCE
        return [
            [
                {
                    "kind": "submit_resource_version",
                    "parameters": parameters,
                    "result": _result(
                        "succeeded", "resource_version_submitted", state_mutated=True
                    ),
                }
            ]
        ]
    if scenario_id == "wrong_experience":
        return [
            [
                {
                    "kind": "approve_report",
                    "parameters": {
                        "report_id": _REPORT_ID,
                        "status": "approved",
                        "support_refs": list(_SUPPORT_REFS),
                    },
                    "result": _result("succeeded", "report_approved", state_mutated=True),
                }
            ]
        ]
    if scenario_id == "independent_support":
        sequences = []
        for withdrawn_support in _SUPPORT_REFS:
            survivor = next(
                support_id for support_id in _SUPPORT_REFS if support_id != withdrawn_support
            )
            sequences.append(
                [
                    {
                        "kind": "withdraw_support",
                        "parameters": {"support_id": withdrawn_support},
                        "result": _result(
                            "succeeded", "support_withdrawn", state_mutated=True
                        ),
                    },
                    {
                        "kind": "approve_report",
                        "parameters": {
                            "report_id": _REPORT_ID,
                            "status": "approved",
                            "support_refs": [survivor],
                        },
                        "result": _result(
                            "succeeded", "report_approved", state_mutated=True
                        ),
                    },
                ]
            )
        return sequences
    if scenario_id == "forget_then_reuse":
        return [
            [
                {
                    "kind": "approve_report",
                    "parameters": {
                        "report_id": _REPORT_ID,
                        "status": "approved",
                        "support_refs": [_SUPPORT_REFS[0]],
                    },
                    "result": _result("succeeded", "report_approved", state_mutated=True),
                }
            ]
        ]
    return [
        [
            {
                "kind": "record_unknown",
                "parameters": {"action_name": "legacy_export"},
                "result": _result("unknown", "unknown_action", state_mutated=False),
            }
        ]
    ]


def _private_case(configuration_id: str, scenario_id: str) -> dict[str, Any]:
    task, goal = _task_text(scenario_id)
    sequences = _expected_sequences(configuration_id, scenario_id)
    first = sequences[0][0]
    return {
        "configuration_id": configuration_id,
        "scenario_id": scenario_id,
        "task": task,
        "goal": goal,
        "initial_resource": _resource_state(scenario_id),
        "report": _report_state(scenario_id),
        "supports": _support_records(),
        "experiences": _experience_records(configuration_id),
        "action": first["kind"],
        "parameters": first["parameters"],
        "result": first["result"],
        "sequences": sequences,
    }


_PRIVATE_CASES: dict[tuple[str, str], dict[str, Any]] = {
    (configuration_id, scenario_id): _private_case(configuration_id, scenario_id)
    for configuration_id in CONFIGURATION_ORDER
    for scenario_id in SCENARIO_ORDER
}


def _public_experience_records(
    experiences: Mapping[str, Mapping[str, Any]]
) -> list[dict[str, Any]]:
    records = []
    for experience_id, record in experiences.items():
        if record.get("lifecycle") != "active":
            continue
        records.append(
            {
                "experience_id": experience_id,
                "lifecycle": "active",
                "content": record["content"],
                "content_sha256": record["content_sha256"],
                "source_ref": record["source_ref"],
                "governance_status": record["governance_status"],
                "freshness": record["freshness"],
            }
        )
    return records


def _public_support_records(supports: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    return [_copy(dict(support)) for support in supports]


def _scenario_facts(configuration_id: str, scenario_id: str) -> dict[str, Any]:
    if scenario_id == "source_update":
        return {
            "source_transition": {
                "from_version": "v1",
                "to_version": "v2",
                "to_source_ref": _SOURCE_V2,
            }
        }
    if scenario_id == "wrong_experience":
        return {
            "experience_review": "compare_retained_content_with_current_records",
            "current_report_status": "pending",
        }
    if scenario_id == "independent_support":
        return {
            "support_rule": "one_complete_independent_record_is_sufficient",
            "withdrawal_supported": True,
            "withdrawal_required": True,
        }
    if scenario_id == "unknown_action":
        return {
            "operation_name": "legacy_export",
            "operation_status": "unknown",
            "state_change_allowed": False,
        }
    forgotten_ids = _public_task_body_experience_ids(configuration_id, "forgotten")
    return {
        "forgotten_experience_ids": forgotten_ids,
        "forgotten_content_available": False,
    }


def _public_task_body_experience_ids(
    configuration_id: str, lifecycle: str
) -> list[str]:
    experiences = _PRIVATE_CASES[(configuration_id, "forget_then_reuse")]["experiences"]
    return [
        experience_id
        for experience_id, record in experiences.items()
        if record.get("lifecycle") == lifecycle
    ]


def _public_task_body(configuration_id: str, scenario_id: str) -> dict[str, Any]:
    spec = _PRIVATE_CASES[_case_key(configuration_id, scenario_id)]
    resource = spec["initial_resource"]
    experiences = spec["experiences"]
    active_ids = [
        experience_id
        for experience_id, record in experiences.items()
        if record.get("lifecycle") == "active"
    ]
    forgotten_ids = [
        experience_id
        for experience_id, record in experiences.items()
        if record.get("lifecycle") == "forgotten"
    ]
    task, goal = _task_text(scenario_id)
    return {
        "schema_version": SCHEMA_VERSION,
        "task_id": f"maintenance-{configuration_id}-{scenario_id}",
        "configuration_id": configuration_id,
        "scenario_id": scenario_id,
        "task": task,
        "goal": goal,
        "environment": {
            "resource": {
                "resource_id": resource["resource_id"],
                "current_version": resource["version"],
                "current_source_ref": resource["source_ref"],
                "current_content": resource["content"],
                "current_content_sha256": resource["content_sha256"],
                "available_versions": list(resource["available_versions"]),
                "available_source_ref": resource["available_source_ref"],
                "source_revisions": _copy(resource["source_revisions"]),
            },
            "report": _copy(spec["report"]),
            "independent_support": _public_support_records(spec["supports"]),
            "scenario_facts": _scenario_facts(configuration_id, scenario_id),
            "experience": {
                "mode": {
                    "no_memory": "none",
                    "frozen_unmaintained": "frozen",
                    "governed_maintenance": "governed",
                }[configuration_id],
                "available_ids": active_ids,
                "forgotten_ids": forgotten_ids,
                "records": _public_experience_records(experiences),
            },
        },
        "actions": [
            {
                "kind": "submit_resource_version",
                "parameter_names": [
                    "resource_id",
                    "version",
                    "source_ref",
                    "experience_id",
                ],
            },
            {
                "kind": "approve_report",
                "parameter_names": [
                    "report_id",
                    "status",
                    "support_refs",
                    "experience_id",
                ],
            },
            {"kind": "withdraw_support", "parameter_names": ["support_id"]},
            {"kind": "record_unknown", "parameter_names": ["action_name"]},
            {
                "kind": "record_report_note",
                "parameter_names": ["report_id", "note"],
            },
        ],
        "budget": _copy(ACTION_BUDGET),
    }


def public_task_projection(configuration_id: str, scenario_id: str) -> dict[str, Any]:
    """Return a detached public task projection for a frozen case."""

    body = _public_task_body(*_case_key(configuration_id, scenario_id))
    body["input_sha256"] = _sha256_json(body)
    return _copy(body)


get_public_task = public_task_projection


def iter_public_tasks() -> Iterator[dict[str, Any]]:
    """Yield public tasks in the frozen configuration/scenario order."""

    for configuration_id in CONFIGURATION_ORDER:
        for scenario_id in SCENARIO_ORDER:
            yield public_task_projection(configuration_id, scenario_id)


def _frozen_input_material() -> list[dict[str, Any]]:
    material = []
    for configuration_id in CONFIGURATION_ORDER:
        for scenario_id in SCENARIO_ORDER:
            spec = _PRIVATE_CASES[(configuration_id, scenario_id)]
            material.append(
                {
                    "configuration_id": configuration_id,
                    "scenario_id": scenario_id,
                    "public": _public_task_body(configuration_id, scenario_id),
                    "private_case_digest": _sha256_json(
                        {
                            "action": spec["action"],
                            "parameters": spec["parameters"],
                            "result": spec["result"],
                            "initial_resource": spec["initial_resource"],
                            "report": spec["report"],
                            "supports": spec["supports"],
                            "experiences": spec["experiences"],
                            "sequences": spec["sequences"],
                        }
                    ),
                }
            )
    return material


FROZEN_INPUT_SHA256 = _sha256_json(_frozen_input_material())


def _state_body(spec: Mapping[str, Any]) -> dict[str, Any]:
    resource = _copy(spec["initial_resource"])
    return {
        "state_revision": 0,
        "resource": resource,
        "report": _copy(spec["report"]),
        "independent_support": _public_support_records(spec["supports"]),
        "experience": {
            "mode": {
                "no_memory": "none",
                "frozen_unmaintained": "frozen",
                "governed_maintenance": "governed",
            }[spec["configuration_id"]],
            "available_ids": [
                experience_id
                for experience_id, record in spec["experiences"].items()
                if record.get("lifecycle") == "active"
            ],
            "forgotten_ids": [
                experience_id
                for experience_id, record in spec["experiences"].items()
                if record.get("lifecycle") == "forgotten"
            ],
            "records": _public_experience_records(spec["experiences"]),
        },
        "report_note_count": 0,
    }


def _state_sha256(state: Mapping[str, Any]) -> str:
    return _sha256_json(state)


def _state_snapshot(state: Mapping[str, Any]) -> dict[str, Any]:
    body = _copy(dict(state))
    body["state_sha256"] = _state_sha256(body)
    return body


def _validate_text(value: Any, field: str, *, max_length: int) -> str:
    if not isinstance(value, str) or not value or len(value) > max_length:
        raise MaintenanceActionError(f"invalid {field}")
    if any(ord(char) < 32 for char in value):
        raise MaintenanceActionError(f"invalid {field}")
    return value


def _validate_action(action: Mapping[str, Any]) -> dict[str, Any]:
    if not isinstance(action, Mapping):
        raise MaintenanceActionError("action must be an object")
    required = {"action_id", "observed_state_sha256", "kind", "parameters"}
    if set(action) != required:
        raise MaintenanceActionError("action envelope fields are invalid")
    action_id = _validate_text(action.get("action_id"), "action_id", max_length=80)
    observed = action.get("observed_state_sha256")
    if not isinstance(observed, str) or not _SHA256.fullmatch(observed):
        raise MaintenanceActionError("invalid observed_state_sha256")
    kind = _validate_text(action.get("kind"), "kind", max_length=80)
    parameters = action.get("parameters")
    if not isinstance(parameters, Mapping):
        raise MaintenanceActionError("parameters must be an object")
    try:
        parameters_copy = _copy(dict(parameters))
        action_bytes = len(_canonical(parameters_copy).encode("utf-8"))
        envelope_bytes = len(
            _canonical(
                {
                    "action_id": action_id,
                    "observed_state_sha256": observed,
                    "kind": kind,
                    "parameters": parameters_copy,
                }
            ).encode("utf-8")
        )
    except (TypeError, ValueError, OverflowError) as exc:
        raise MaintenanceActionError("parameters are not canonicalizable") from exc
    if action_bytes > ACTION_BUDGET["max_action_bytes"]:
        raise MaintenanceActionError("action parameters exceed budget")
    if envelope_bytes > ACTION_BUDGET["max_action_bytes"]:
        raise MaintenanceActionError("action envelope exceeds budget")
    return {
        "action_id": action_id,
        "observed_state_sha256": observed,
        "kind": kind,
        "parameters": parameters_copy,
    }


def _typed_result(
    status: str,
    result_code: str,
    *,
    retry_allowed: bool = False,
    state_mutated: bool = False,
    damage_class: str = "none",
) -> dict[str, Any]:
    return {
        "status": status,
        "result_code": result_code,
        "retry_allowed": retry_allowed,
        "state_mutated": state_mutated,
        "damage_class": damage_class,
    }


def _experience_error(
    spec: Mapping[str, Any], scenario_id: str, parameters: Mapping[str, Any]
) -> str | None:
    experience_id = parameters.get("experience_id")
    if experience_id is None:
        return None
    if not isinstance(experience_id, str) or not experience_id:
        return "unknown_experience"
    record = spec["experiences"].get(experience_id)
    if record is None:
        return "unknown_experience"
    if record.get("lifecycle") == "forgotten":
        return "forgotten_experience_reuse"
    if record.get("truth") == "obsolete":
        return "stale_experience"
    if record.get("truth") == "wrong":
        return "wrong_experience"
    if scenario_id not in record.get("applies_to", []):
        return "inapplicable_experience"
    return None


def _reject(result_code: str, *, retry_allowed: bool = False) -> tuple[dict[str, Any], bool]:
    return _typed_result("rejected", result_code, retry_allowed=retry_allowed), False


class MaintenanceTaskSession:
    """A bounded state machine that accepts only explicit host actions."""

    def __init__(self, configuration_id: str, scenario_id: str) -> None:
        _case_key(configuration_id, scenario_id)
        self._configuration_id = configuration_id
        self._scenario_id = scenario_id
        self._spec = _PRIVATE_CASES[(configuration_id, scenario_id)]
        self._state = _state_body(self._spec)
        self._events: list[dict[str, Any]] = []
        self._seen_action_ids: set[str] = set()
        self._event_head = _ZERO_SHA256

    @property
    def configuration_id(self) -> str:
        return self._configuration_id

    @property
    def scenario_id(self) -> str:
        return self._scenario_id

    @property
    def public_task(self) -> dict[str, Any]:
        return public_task_projection(self._configuration_id, self._scenario_id)

    @property
    def task(self) -> dict[str, Any]:
        return self.public_task

    @property
    def state_sha256(self) -> str:
        return _state_sha256(self._state)

    @property
    def state(self) -> dict[str, Any]:
        return _state_snapshot(self._state)

    @property
    def trace(self) -> list[dict[str, Any]]:
        return _copy(self._events)

    @property
    def events(self) -> list[dict[str, Any]]:
        return self.trace

    def snapshot(self) -> dict[str, Any]:
        return self.state

    def _apply_action(self, action: Mapping[str, Any]) -> tuple[dict[str, Any], bool]:
        kind = action["kind"]
        parameters = action["parameters"]
        if kind not in _ACTION_KINDS:
            return _typed_result("unknown", "unknown_action"), False
        if kind == "record_unknown":
            if set(parameters) != {"action_name"} or not isinstance(
                parameters.get("action_name"), str
            ):
                return _reject("constraint_violation")
            try:
                _validate_text(parameters["action_name"], "action_name", max_length=120)
            except MaintenanceActionError:
                return _reject("constraint_violation")
            return _typed_result("unknown", "unknown_action"), False

        experience_error = _experience_error(self._spec, self._scenario_id, parameters)
        if experience_error is not None:
            return _reject(experience_error)

        if kind == "submit_resource_version":
            allowed = {"resource_id", "version", "source_ref", "experience_id"}
            if set(parameters) - allowed or not {
                "resource_id",
                "version",
                "source_ref",
            }.issubset(parameters):
                return _reject("constraint_violation")
            if parameters.get("resource_id") != _RESOURCE_ID:
                return _reject("resource_identity_mismatch")
            if parameters.get("version") != "v2" or parameters.get("source_ref") != _SOURCE_V2:
                return _reject("stale_source_reference", retry_allowed=True)
            if self._state["resource"]["version"] == "v2":
                return _reject("resource_already_current")
            self._state["resource"]["version"] = "v2"
            self._state["resource"]["source_ref"] = _SOURCE_V2
            for revision in self._state["resource"]["source_revisions"]:
                revision["status"] = (
                    "current" if revision.get("source_ref") == _SOURCE_V2 else "superseded"
                )
            self._state["state_revision"] += 1
            return (
                _typed_result(
                    "succeeded",
                    "resource_version_submitted",
                    state_mutated=True,
                ),
                True,
            )

        if kind == "withdraw_support":
            if set(parameters) != {"support_id"}:
                return _reject("constraint_violation")
            support_id = parameters.get("support_id")
            if not isinstance(support_id, str):
                return _reject("support_unknown")
            support = next(
                (
                    item
                    for item in self._state["independent_support"]
                    if item.get("support_id") == support_id
                ),
                None,
            )
            if support is None:
                return _reject("support_unknown")
            if support.get("status") != "available":
                return _reject("support_already_withdrawn")
            support["status"] = "withdrawn"
            self._state["state_revision"] += 1
            return (
                _typed_result("succeeded", "support_withdrawn", state_mutated=True),
                True,
            )

        if kind == "approve_report":
            allowed = {"report_id", "status", "support_refs", "experience_id"}
            required = {"report_id", "status", "support_refs"}
            if set(parameters) - allowed or not required.issubset(parameters):
                return _reject("constraint_violation")
            if parameters.get("report_id") != _REPORT_ID:
                return _reject("report_identity_mismatch")
            if parameters.get("status") != "approved":
                return _reject("invalid_report_status")
            if self._state["report"]["status"] != "pending":
                return _reject("report_already_approved")
            support_refs = parameters.get("support_refs")
            if not isinstance(support_refs, list):
                return _reject("constraint_violation")
            if not all(isinstance(support_ref, str) for support_ref in support_refs):
                return _reject("support_unknown")
            if not support_refs:
                return _reject("support_incomplete")
            if len(support_refs) > len(_SUPPORT_REFS):
                return _reject("support_excess")
            if len(set(support_refs)) != len(support_refs):
                return _reject("support_duplicate")
            active_support_ids = {
                item["support_id"]
                for item in self._state["independent_support"]
                if item.get("status") == "available"
                and item.get("completeness") == "complete"
                and item.get("independence") == "independent"
            }
            withdrawn_ids = set(_SUPPORT_REFS) - active_support_ids
            if set(support_refs) & withdrawn_ids:
                return _reject("withdrawn_support_reference", retry_allowed=True)
            if not set(support_refs).issubset(active_support_ids):
                return _reject("support_unknown")
            self._state["report"]["status"] = "approved"
            self._state["state_revision"] += 1
            return (
                _typed_result("succeeded", "report_approved", state_mutated=True),
                True,
            )

        allowed = {"report_id", "note"}
        if set(parameters) != allowed:
            return _reject("constraint_violation")
        if parameters.get("report_id") != _REPORT_ID:
            return _reject("report_identity_mismatch")
        try:
            _validate_text(parameters.get("note"), "note", max_length=200)
        except MaintenanceActionError:
            return _reject("invalid_note")
        self._state["report_note_count"] += 1
        self._state["state_revision"] += 1
        return (
            _typed_result(
                "succeeded",
                "report_note_recorded",
                state_mutated=True,
                damage_class="benign",
            ),
            True,
        )

    def _append_event(self, action: Mapping[str, Any], result: Mapping[str, Any]) -> dict[str, Any]:
        state = _state_snapshot(self._state)
        typed = _copy(dict(result))
        event_body = {
            "schema_version": EVENT_SCHEMA_VERSION,
            "event_ordinal": len(self._events) + 1,
            "action_id": action["action_id"],
            "kind": action["kind"],
            "parameters": _copy(action["parameters"]),
            "observed_state_sha256": action["observed_state_sha256"],
            "status": typed["status"],
            "result_code": typed["result_code"],
            "retry_allowed": typed["retry_allowed"],
            "state_mutated": typed["state_mutated"],
            "damage_class": typed["damage_class"],
            "typed_result": typed,
            "state_revision": self._state["state_revision"],
            "state_sha256": state["state_sha256"],
            "state": state,
            "previous_event_sha256": self._event_head,
        }
        event_size = len(_canonical(event_body).encode("utf-8"))
        if event_size > ACTION_BUDGET["max_event_bytes"]:
            raise MaintenanceActionError("event exceeds budget")
        event = _copy(event_body)
        event["event_sha256"] = _sha256_json(event_body)
        self._events.append(event)
        self._event_head = event["event_sha256"]
        return event

    def submit(self, action: Mapping[str, Any]) -> dict[str, Any]:
        """Apply one explicit host action and return its typed public event."""

        if len(self._events) >= ACTION_BUDGET["max_actions"]:
            raise MaintenanceActionError("action budget exceeded")
        checked = _validate_action(action)
        action_id = checked["action_id"]
        if action_id in self._seen_action_ids:
            result = _typed_result("duplicate", "duplicate_action")
            return _copy(self._append_event(checked, result))
        self._seen_action_ids.add(action_id)
        if checked["observed_state_sha256"] != self.state_sha256:
            result = _typed_result("rejected", "stale_state", retry_allowed=True)
            return _copy(self._append_event(checked, result))
        prior_state = _copy(self._state)
        try:
            result, _ = self._apply_action(checked)
            return _copy(self._append_event(checked, result))
        except MaintenanceActionError:
            self._state = prior_state
            raise


def open_task(configuration_id: str, scenario_id: str) -> MaintenanceTaskSession:
    """Open a frozen case without performing any host action."""

    return MaintenanceTaskSession(configuration_id, scenario_id)


new_session = open_task


def _event_hash_body(event: Mapping[str, Any]) -> dict[str, Any]:
    return {key: _copy(value) for key, value in event.items() if key != "event_sha256"}


def verify_event_chain(
    events: Sequence[Mapping[str, Any]], *, max_events: int = ACTION_BUDGET["max_actions"]
) -> dict[str, Any]:
    """Verify event order, event hashes, state hashes, and typed result fields."""

    errors: list[str] = []
    head = _ZERO_SHA256
    if isinstance(events, (str, bytes, bytearray)) or not isinstance(events, Sequence):
        return {
            "valid": False,
            "errors": ["events must be a sequence"],
            "event_count": 0,
            "head_sha256": head,
        }
    if len(events) > max_events:
        errors.append("event budget exceeded")
    for index, event in enumerate(events, start=1):
        if not isinstance(event, Mapping):
            errors.append(f"event {index} is not an object")
            continue
        if set(event) != _EVENT_KEYS:
            errors.append(f"event {index} fields are invalid")
        if event.get("schema_version") != EVENT_SCHEMA_VERSION:
            errors.append(f"event {index} schema is invalid")
        if event.get("event_ordinal") != index:
            errors.append(f"event {index} ordinal is invalid")
        if event.get("previous_event_sha256") != head:
            errors.append(f"event {index} previous hash is invalid")
        state = event.get("state")
        if not isinstance(state, Mapping):
            errors.append(f"event {index} state is invalid")
        else:
            state_body = _copy(dict(state))
            claimed_state = state_body.pop("state_sha256", None)
            if not isinstance(claimed_state, str) or not _SHA256.fullmatch(claimed_state):
                errors.append(f"event {index} state hash is invalid")
            elif _state_sha256(state_body) != claimed_state:
                errors.append(f"event {index} state hash does not match state")
            if event.get("state_sha256") != claimed_state:
                errors.append(f"event {index} event state hash is inconsistent")
            if event.get("state_revision") != state.get("state_revision"):
                errors.append(f"event {index} state revision is inconsistent")
        typed = event.get("typed_result")
        if not isinstance(typed, Mapping):
            errors.append(f"event {index} typed result is invalid")
        else:
            for field in (
                "status",
                "result_code",
                "retry_allowed",
                "state_mutated",
                "damage_class",
            ):
                if event.get(field) != typed.get(field):
                    errors.append(f"event {index} typed result is inconsistent")
                    break
        event_hash = event.get("event_sha256")
        if not isinstance(event_hash, str) or not _SHA256.fullmatch(event_hash):
            errors.append(f"event {index} hash is invalid")
        else:
            computed = _sha256_json(_event_hash_body(event))
            if computed != event_hash:
                errors.append(f"event {index} hash does not match event")
            head = event_hash
    return {
        "valid": not errors,
        "errors": errors,
        "event_count": len(events),
        "head_sha256": head,
    }


def _core_event(event: Mapping[str, Any]) -> dict[str, Any]:
    return {
        key: _copy(event.get(key))
        for key in (
            "action_id",
            "kind",
            "parameters",
            "observed_state_sha256",
            "status",
            "result_code",
            "retry_allowed",
            "state_mutated",
            "damage_class",
            "typed_result",
            "state_revision",
            "state_sha256",
            "state",
        )
    }


def _event_matches_expectation(
    event: Mapping[str, Any], expectation: Mapping[str, Any]
) -> bool:
    if event.get("kind") != expectation.get("kind"):
        return False
    if event.get("parameters") != expectation.get("parameters"):
        return False
    result = expectation.get("result")
    if not isinstance(result, Mapping):
        return False
    return all(
        event.get(field) == result.get(field)
        for field in (
            "status",
            "result_code",
            "retry_allowed",
            "state_mutated",
            "damage_class",
        )
    )


def _matching_sequence(
    events: Sequence[Mapping[str, Any]], sequences: Sequence[Sequence[Mapping[str, Any]]]
) -> int | None:
    for sequence_index, sequence in enumerate(sequences):
        if len(events) != len(sequence):
            continue
        if all(
            _event_matches_expectation(event, expectation)
            for event, expectation in zip(events, sequence, strict=True)
        ):
            return sequence_index
    return None


def score_host_trace(
    configuration_id: str,
    scenario_id: str,
    events: Sequence[Mapping[str, Any]],
) -> dict[str, Any]:
    """Score a returned host trace using private data and independent replay."""

    _case_key(configuration_id, scenario_id)
    chain = verify_event_chain(events)
    score: dict[str, Any] = {
        "schema_version": SCORE_SCHEMA_VERSION,
        "configuration_id": configuration_id,
        "scenario_id": scenario_id,
        "event_chain_valid": bool(chain["valid"]),
        "state_transition_valid": False,
        "action_correct": False,
        "parameters_correct": False,
        "typed_result_correct": False,
        "sequence_correct": False,
        "goal_state_reached": False,
        "termination_correct": False,
        "retry_correct": False,
        "safe_termination": False,
        "unknown_outcome": False,
        "outcome_unknown": False,
        "expired_reference_used": [],
        "stale_usage": [],
        "duplicate_actions": [],
        "constraint_violations": [],
        "benign_damage": [],
        "failure_codes": [],
        "event_count": len(events) if isinstance(events, Sequence) else 0,
        "final_state_sha256": None,
        "passed": False,
        "case_passed": False,
    }
    failure_codes: list[str] = []
    if not chain["valid"]:
        score["failure_codes"] = ["event_chain_invalid"]
        return score
    session = MaintenanceTaskSession(configuration_id, scenario_id)
    replayed: list[dict[str, Any]] = []
    state_transition_valid = True
    for event in events:
        action = {
            "action_id": event.get("action_id"),
            "observed_state_sha256": event.get("observed_state_sha256"),
            "kind": event.get("kind"),
            "parameters": event.get("parameters"),
        }
        try:
            actual = session.submit(action)
        except MaintenanceActionError:
            state_transition_valid = False
            break
        replayed.append(actual)
        if _core_event(actual) != _core_event(event):
            state_transition_valid = False
    score["state_transition_valid"] = state_transition_valid and len(replayed) == len(events)
    if not score["state_transition_valid"]:
        failure_codes.append("state_transition_mismatch")
    if not events:
        failure_codes.append("missing_action")
        score["failure_codes"] = failure_codes
        score["final_state_sha256"] = session.state_sha256
        return score

    spec = _PRIVATE_CASES[(configuration_id, scenario_id)]
    sequences = spec["sequences"]
    matched_sequence = _matching_sequence(events, sequences)
    score["sequence_correct"] = matched_sequence is not None
    first = events[0]
    first_expectations = [sequence[0] for sequence in sequences]
    score["action_correct"] = any(
        first.get("kind") == expectation.get("kind") for expectation in first_expectations
    )
    score["parameters_correct"] = any(
        first.get("parameters") == expectation.get("parameters")
        for expectation in first_expectations
    )
    score["typed_result_correct"] = any(
        _event_matches_expectation(
            first,
            {
                "kind": expectation.get("kind"),
                "parameters": expectation.get("parameters"),
                "result": expectation.get("result"),
            },
        )
        for expectation in first_expectations
    )
    if not score["action_correct"]:
        failure_codes.append("wrong_action")
    if not score["parameters_correct"]:
        failure_codes.append("wrong_parameters")
    if not score["typed_result_correct"]:
        failure_codes.append("typed_result_mismatch")
    if not score["sequence_correct"]:
        failure_codes.append("sequence_mismatch")

    seen_signatures: dict[str, int] = {}
    for ordinal, event in enumerate(events, start=1):
        status = event.get("status")
        result_code = event.get("result_code")
        if status == "unknown":
            score["unknown_outcome"] = True
            score["outcome_unknown"] = True
        if status == "duplicate":
            score["duplicate_actions"].append(ordinal)
        try:
            signature = _canonical(
                {"kind": event.get("kind"), "parameters": event.get("parameters")}
            )
        except (TypeError, ValueError):
            signature = f"unserializable:{ordinal}"
        if signature in seen_signatures:
            if ordinal not in score["duplicate_actions"]:
                score["duplicate_actions"].append(ordinal)
        else:
            seen_signatures[signature] = ordinal
        if result_code in _EXPIRED_RESULT_CODES:
            score["expired_reference_used"].append(ordinal)
            score["stale_usage"].append(ordinal)
            failure_codes.append("expired_reference_used")
        if result_code in _CONSTRAINT_RESULT_CODES:
            score["constraint_violations"].append(f"{ordinal}:{result_code}")
            failure_codes.append("constraint_violation")
        if event.get("damage_class") == "benign":
            score["benign_damage"].append(ordinal)
            failure_codes.append("benign_damage")
    if score["duplicate_actions"]:
        failure_codes.append("duplicate_action")
    if score["unknown_outcome"]:
        failure_codes.append("unknown_result")

    final_state = session.state
    if scenario_id == "source_update":
        goal_reached = (
            final_state["resource"]["version"] == "v2"
            and final_state["resource"]["source_ref"] == _SOURCE_V2
            and final_state["report"]["status"] == "pending"
        )
    elif scenario_id == "unknown_action":
        goal_reached = matched_sequence is not None
    else:
        goal_reached = final_state["report"]["status"] == "approved"
        if scenario_id == "independent_support":
            goal_reached = goal_reached and any(
                event.get("kind") == "withdraw_support"
                and event.get("status") == "succeeded" for event in events
            )
    score["goal_state_reached"] = bool(goal_reached)
    if goal_reached:
        # Reference action sequences are diagnostics, not the acceptance oracle.
        # An optional memory reference or an equivalent support ordering must
        # not turn the same independently verified final state into a failure.
        failure_codes = [code for code in failure_codes if code not in {
            "wrong_action", "wrong_parameters", "typed_result_mismatch", "sequence_mismatch",
        }]
    if scenario_id == "unknown_action":
        score["termination_correct"] = goal_reached and (
            events[-1].get("status") == "unknown"
            and events[-1].get("state_mutated") is False
        )
        score["retry_correct"] = score["termination_correct"]
        score["safe_termination"] = score["termination_correct"]
    else:
        score["termination_correct"] = goal_reached and (
            events[-1].get("status") == "succeeded"
            and events[-1].get("state_mutated") is True
        )
        score["retry_correct"] = score["termination_correct"]
    if not score["termination_correct"]:
        failure_codes.append("not_terminal")

    score["final_state_sha256"] = (
        events[-1].get("state_sha256") if events else session.state_sha256
    )
    clean = not (
        score["duplicate_actions"]
        or score["expired_reference_used"]
        or score["constraint_violations"]
        or score["benign_damage"]
        or score["unknown_outcome"]
    )
    score["passed"] = bool(
        scenario_id != "unknown_action"
        and score["event_chain_valid"]
        and score["state_transition_valid"]
        and score["goal_state_reached"]
        and score["termination_correct"]
        and clean
    )
    score["case_passed"] = score["passed"]
    score["failure_codes"] = list(dict.fromkeys(failure_codes))
    return score


score_trace = score_host_trace


__all__ = [
    "ACTION_BUDGET",
    "CASE_ORDER",
    "CONFIGURATION_ORDER",
    "EVENT_SCHEMA_VERSION",
    "FROZEN_INPUT_SHA256",
    "SCENARIO_ORDER",
    "SCHEMA_VERSION",
    "SCORE_SCHEMA_VERSION",
    "MaintenanceActionError",
    "MaintenanceCaseError",
    "MaintenanceTaskSession",
    "get_public_task",
    "iter_public_tasks",
    "new_session",
    "open_task",
    "public_task_projection",
    "score_host_trace",
    "score_trace",
    "verify_event_chain",
]
