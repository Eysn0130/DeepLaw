"""Synthetic policy and cross-binding regressions for the candidate isolation source."""

from __future__ import annotations

import copy
import hashlib
import json
from pathlib import Path
from types import MappingProxyType
from typing import Any

import pytest
from jsonschema import Draft202012Validator

from benchmarks.hosts.owner_guard_isolation import (
    ACTOR_ROLES,
    BINDING_FIELDS,
    SCHEMA_FILENAME,
    SCHEMA_VERSION,
    OwnerGuardIsolationError,
    validate_owner_guard_isolation,
)

CONTRACTS = Path(__file__).resolve().parents[1] / "contracts"
ENVELOPE_FIELDS = (
    "candidate_binding",
    "run_binding",
    "corpus",
    "runner",
    "scorer",
    "host",
    "task_case",
)


def digest(label: str) -> str:
    return hashlib.sha256(label.encode("ascii")).hexdigest()


def synthetic_source(task_case: str = "continuity") -> dict[str, Any]:
    return {
        "schema_version": SCHEMA_VERSION,
        "artifact_kind": "isolation_receipt",
        "candidate_binding": {
            "commit": "a" * 40,
            "tree": "b" * 40,
            "lock_sha256": digest("lock"),
            "wheel_sha256": digest("wheel"),
            "sdist_sha256": digest("sdist"),
        },
        "run_binding": {"run_id": "synthetic-run", "workflow_run_id": 1},
        "corpus": {"role": "host_qualification", "sha256": digest("corpus")},
        "runner": {"identity": "synthetic-runner", "sha256": digest("runner-source")},
        "scorer": {"identity": "synthetic-scorer", "sha256": digest("scorer-source")},
        "host": "opencode",
        "task_case": task_case,
        "actors": {
            role: {
                "process_identity_sha256": digest(role + "-process"),
                "source_sha256": digest(role + "-source"),
                "instance_sha256": digest(role + "-instance"),
            }
            for role in ACTOR_ROLES
        },
        "credential_observation": {
            "mode": "owner_external_guard_host_nonce_mcp_no_key",
            "authority_key_present": True,
            "key_received": {role: False for role in ACTOR_ROLES[1:]},
        },
        "inspection_observation": {
            "authority_public_request_inspected": True,
            "observer_public_native_response_inspected": False,
            "raw_request_retained": False,
            "raw_native_response_retained": False,
            "store_reads": {
                role: {
                    "auth_store_read": False,
                    "transcript_store_read": False,
                    "reasoning_store_read": False,
                }
                for role in ACTOR_ROLES
            },
        },
        "process_boundary": {
            "native_receipt_observed": True,
            "host_process_separated": True,
            "mcp_process_separated": True,
        },
        "write_observation": {
            "hidden_mutation": False,
            "write_performed": False,
            "authorized_mutation": {
                "observed": False,
                "operation": None,
                "owner_authorized": False,
                "receipt_sha256": None,
            },
            "audit_head_before_sha256": digest("audit-before"),
            "audit_head_after_sha256": digest("audit-before"),
        },
        "claim_eligible": False,
        **{field: digest(field) for field in BINDING_FIELDS},
    }


def envelope_for(source: dict[str, Any]) -> dict[str, Any]:
    return copy.deepcopy({field: source[field] for field in ENVELOPE_FIELDS})


def bindings_for(source: dict[str, Any]) -> dict[str, str]:
    return {field: source[field] for field in BINDING_FIELDS}


def validate(source: dict[str, Any]) -> dict[str, Any]:
    return validate_owner_guard_isolation(
        source, envelope=envelope_for(source), expected_bindings=bindings_for(source)
    )


def node(source: dict[str, Any], path: tuple[str, ...]) -> dict[str, Any]:
    result = source
    for field in path:
        result = result[field]
    return result


@pytest.mark.parametrize("task_case", ("continuity", "living_wiki", "professional_evidence"))
@pytest.mark.parametrize("response_inspected", (False, True))
def test_valid_public_inspection_is_a_declaration_without_formal_admission(
    task_case: str, response_inspected: bool
) -> None:
    source = synthetic_source(task_case)
    source["inspection_observation"]["observer_public_native_response_inspected"] = (
        response_inspected
    )
    original = copy.deepcopy(source)
    result = validate(source)
    assert set(result) == {"formal_admission", "validated_source", "record_sha256"}
    assert result["formal_admission"] is False
    assert result["validated_source"] == original
    assert result["validated_source"]["claim_eligible"] is False
    expected_raw = json.dumps(
        original, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False
    ).encode()
    assert result["record_sha256"] == hashlib.sha256(expected_raw).hexdigest()
    assert source == original
    result["validated_source"]["actors"]["host"]["source_sha256"] = digest("changed-output")
    assert source == original


def test_canonical_digest_does_not_depend_on_mapping_order() -> None:
    source = synthetic_source()
    result = validate(source)
    reordered = dict(reversed(list(source.items())))
    reordered["actors"] = dict(reversed(list(source["actors"].items())))
    assert validate(reordered)["record_sha256"] == result["record_sha256"]


def test_existing_envelope_is_projected_without_adding_native_binding_fields() -> None:
    source = synthetic_source()
    envelope = envelope_for(source)
    envelope["other_typed_envelope_field"] = {"unrelated": "not-a-new-source-field"}
    before = copy.deepcopy(envelope)
    result = validate_owner_guard_isolation(
        source, envelope=envelope, expected_bindings=bindings_for(source)
    )
    assert envelope == before
    assert not set(BINDING_FIELDS) & set(envelope)
    assert "other_typed_envelope_field" not in result["validated_source"]


def test_mapping_inputs_are_copied_without_mutation() -> None:
    source = synthetic_source()
    source["actors"] = MappingProxyType(source["actors"])
    result = validate_owner_guard_isolation(
        MappingProxyType(source),
        envelope=MappingProxyType(envelope_for(source)),
        expected_bindings=MappingProxyType(bindings_for(source)),
    )
    assert type(result["validated_source"]) is dict
    assert type(result["validated_source"]["actors"]) is dict


@pytest.mark.parametrize("field", ENVELOPE_FIELDS)
def test_existing_metadata_must_match_envelope_exactly(field: str) -> None:
    source = synthetic_source()
    envelope = envelope_for(source)
    if field == "candidate_binding":
        source[field]["commit"] = "c" * 40
    elif field == "run_binding":
        source[field]["workflow_run_id"] = 2
    elif field == "corpus":
        source[field]["sha256"] = digest("different-corpus")
    elif field in ("runner", "scorer"):
        source[field]["identity"] = "different-identity"
    elif field == "host":
        envelope[field] = "codex"
    else:
        source[field] = "living_wiki"
    code = "owner_guard_envelope_invalid" if field == "host" else "owner_guard_binding_mismatch"
    with pytest.raises(OwnerGuardIsolationError, match="^" + code + "$"):
        validate_owner_guard_isolation(
            source, envelope=envelope, expected_bindings=bindings_for(source)
        )


@pytest.mark.parametrize("field", BINDING_FIELDS)
def test_native_binding_must_match_separate_expected_value(field: str) -> None:
    source = synthetic_source()
    bindings = bindings_for(source)
    source[field] = digest("different-binding")
    with pytest.raises(OwnerGuardIsolationError, match=r"^owner_guard_binding_mismatch$"):
        validate_owner_guard_isolation(
            source, envelope=envelope_for(source), expected_bindings=bindings
        )


@pytest.mark.parametrize("field", BINDING_FIELDS)
@pytest.mark.parametrize("bad_value", (None, True, "0" * 64, "F" * 64, "not-a-digest"))
def test_native_bindings_are_nonzero_lowercase_digests(field: str, bad_value: Any) -> None:
    source = synthetic_source()
    source[field] = bad_value
    with pytest.raises(OwnerGuardIsolationError, match=r"^owner_guard_shape_invalid$"):
        validate(source)


@pytest.mark.parametrize("change", ("extra", "missing", "none", "zero"))
def test_expected_bindings_are_a_separate_closed_mapping(change: str) -> None:
    source = synthetic_source()
    bindings = bindings_for(source)
    if change == "extra":
        bindings["raw_payload"] = "PUBLIC_SYNTHETIC_CANARY"
    elif change == "missing":
        del bindings[BINDING_FIELDS[0]]
    elif change == "none":
        bindings[BINDING_FIELDS[0]] = None
    else:
        bindings[BINDING_FIELDS[0]] = "0" * 64
    with pytest.raises(OwnerGuardIsolationError, match=r"^owner_guard_expected_bindings_invalid$"):
        validate_owner_guard_isolation(
            source, envelope=envelope_for(source), expected_bindings=bindings
        )


@pytest.mark.parametrize("role", ACTOR_ROLES)
@pytest.mark.parametrize("field", ("process_identity_sha256", "source_sha256", "instance_sha256"))
def test_every_actor_metadata_digest_is_nonzero(role: str, field: str) -> None:
    source = synthetic_source()
    source["actors"][role][field] = "0" * 64
    with pytest.raises(OwnerGuardIsolationError, match=r"^owner_guard_shape_invalid$"):
        validate(source)


@pytest.mark.parametrize(
    "isolated_role,other_role",
    tuple((role, other) for role in ACTOR_ROLES[:3] for other in ACTOR_ROLES if role != other),
)
def test_authority_host_and_mcp_cannot_share_another_role_process(
    isolated_role: str, other_role: str
) -> None:
    source = synthetic_source()
    source["actors"][isolated_role]["process_identity_sha256"] = source["actors"][other_role][
        "process_identity_sha256"
    ]
    with pytest.raises(OwnerGuardIsolationError, match=r"^owner_guard_role_overlap$"):
        validate(source)


@pytest.mark.parametrize(
    "roles",
    (
        ("observer", "runner"),
        ("runner", "scorer"),
        ("observer", "scorer"),
        ("observer", "runner", "scorer"),
    ),
)
def test_external_observation_roles_can_share_process_and_instance_with_distinct_sources(
    roles: tuple[str, ...],
) -> None:
    source = synthetic_source()
    for role in roles:
        source["actors"][role]["process_identity_sha256"] = digest("shared-external-process")
        source["actors"][role]["instance_sha256"] = digest("shared-external-instance")
    result = validate(source)
    assert result["formal_admission"] is False
    assert len(
        {result["validated_source"]["actors"][role]["source_sha256"] for role in roles}
    ) == len(roles)


@pytest.mark.parametrize(
    "roles", (("observer", "runner"), ("runner", "scorer"), ("observer", "scorer"))
)
def test_shared_external_process_requires_same_instance(roles: tuple[str, str]) -> None:
    source = synthetic_source()
    first, second = roles
    source["actors"][second]["process_identity_sha256"] = source["actors"][first][
        "process_identity_sha256"
    ]
    with pytest.raises(OwnerGuardIsolationError, match=r"^owner_guard_instance_mismatch$"):
        validate(source)


@pytest.mark.parametrize("role", ("runner", "scorer"))
def test_actor_source_matches_top_level_runner_or_scorer_binding(role: str) -> None:
    source = synthetic_source()
    source["actors"][role]["source_sha256"] = digest("wrong-actor-source")
    with pytest.raises(OwnerGuardIsolationError, match=r"^owner_guard_actor_source_mismatch$"):
        validate(source)


OBJECT_PATHS = (
    (),
    ("candidate_binding",),
    ("run_binding",),
    ("corpus",),
    ("runner",),
    ("scorer",),
    ("actors",),
    *(("actors", role) for role in ACTOR_ROLES),
    ("credential_observation",),
    ("credential_observation", "key_received"),
    ("inspection_observation",),
    ("inspection_observation", "store_reads"),
    *(("inspection_observation", "store_reads", role) for role in ACTOR_ROLES),
    ("process_boundary",),
    ("write_observation",),
    ("write_observation", "authorized_mutation"),
)


@pytest.mark.parametrize("path", OBJECT_PATHS)
def test_unknown_fields_at_every_object_fail_closed_without_canary_text(
    path: tuple[str, ...],
) -> None:
    source = synthetic_source()
    node(source, path)["private_path"] = "/private/PUBLIC_SYNTHETIC_CANARY"
    with pytest.raises(OwnerGuardIsolationError) as error:
        validate(source)
    assert str(error.value) == "owner_guard_shape_invalid"
    assert "CANARY" not in str(error.value)
    assert "/private/" not in str(error.value)
    assert error.value.__cause__ is None


@pytest.mark.parametrize(
    "field",
    (
        "actors",
        "credential_observation",
        "inspection_observation",
        "process_boundary",
        "write_observation",
    ),
)
@pytest.mark.parametrize("change", ("missing", "none"))
def test_missing_or_none_observations_fail_closed(field: str, change: str) -> None:
    source = synthetic_source()
    if change == "missing":
        del source[field]
    else:
        source[field] = None
    with pytest.raises(OwnerGuardIsolationError, match=r"^owner_guard_shape_invalid$"):
        validate(source)


POLICY_FALSE_PATHS = (
    ("claim_eligible",),
    *(("credential_observation", "key_received", role) for role in ACTOR_ROLES[1:]),
    ("inspection_observation", "raw_request_retained"),
    ("inspection_observation", "raw_native_response_retained"),
    *(
        ("inspection_observation", "store_reads", role, field)
        for role in ACTOR_ROLES
        for field in ("auth_store_read", "transcript_store_read", "reasoning_store_read")
    ),
    ("write_observation", "hidden_mutation"),
)


@pytest.mark.parametrize("path", POLICY_FALSE_PATHS)
def test_no_key_delivery_retention_store_read_hidden_write_or_claim(path: tuple[str, ...]) -> None:
    source = synthetic_source()
    node(source, path[:-1])[path[-1]] = True
    with pytest.raises(OwnerGuardIsolationError, match=r"^owner_guard_shape_invalid$"):
        validate(source)


POLICY_TRUE_PATHS = (
    ("credential_observation", "authority_key_present"),
    ("inspection_observation", "authority_public_request_inspected"),
    ("process_boundary", "native_receipt_observed"),
    ("process_boundary", "host_process_separated"),
    ("process_boundary", "mcp_process_separated"),
)


@pytest.mark.parametrize("path", POLICY_TRUE_PATHS)
def test_required_isolation_observations_cannot_be_false(path: tuple[str, ...]) -> None:
    source = synthetic_source()
    node(source, path[:-1])[path[-1]] = False
    with pytest.raises(OwnerGuardIsolationError, match=r"^owner_guard_shape_invalid$"):
        validate(source)


@pytest.mark.parametrize(
    "path",
    POLICY_FALSE_PATHS
    + POLICY_TRUE_PATHS
    + (
        ("inspection_observation", "observer_public_native_response_inspected"),
        ("write_observation", "write_performed"),
        ("write_observation", "authorized_mutation", "observed"),
        ("write_observation", "authorized_mutation", "owner_authorized"),
    ),
)
def test_integer_cannot_impersonate_a_boolean_observation(path: tuple[str, ...]) -> None:
    source = synthetic_source()
    node(source, path[:-1])[path[-1]] = int(node(source, path[:-1])[path[-1]])
    with pytest.raises(OwnerGuardIsolationError, match=r"^owner_guard_shape_invalid$"):
        validate(source)


@pytest.mark.parametrize("bad_value", (True, 1.0, None))
@pytest.mark.parametrize("target", ("source", "envelope"))
def test_workflow_run_id_keeps_strict_integer_semantics(bad_value: Any, target: str) -> None:
    source = synthetic_source()
    envelope = envelope_for(source)
    selected = source if target == "source" else envelope
    selected["run_binding"]["workflow_run_id"] = bad_value
    code = "owner_guard_shape_invalid" if target == "source" else "owner_guard_envelope_invalid"
    with pytest.raises(OwnerGuardIsolationError, match="^" + code + "$"):
        validate_owner_guard_isolation(
            source, envelope=envelope, expected_bindings=bindings_for(source)
        )


@pytest.mark.parametrize("target", ("source", "envelope", "bindings"))
def test_non_mapping_input_is_rejected_with_fixed_error(target: str) -> None:
    source = synthetic_source()
    inputs = {"source": source, "envelope": envelope_for(source), "bindings": bindings_for(source)}
    inputs[target] = None
    code = {
        "source": "owner_guard_shape_invalid",
        "envelope": "owner_guard_envelope_invalid",
        "bindings": "owner_guard_expected_bindings_invalid",
    }[target]
    with pytest.raises(OwnerGuardIsolationError, match="^" + code + "$"):
        validate_owner_guard_isolation(
            inputs["source"], envelope=inputs["envelope"], expected_bindings=inputs["bindings"]
        )


@pytest.mark.parametrize("host", ("codex", "Core"))
def test_no_extra_host_slot(host: str) -> None:
    source = synthetic_source()
    source["host"] = host
    with pytest.raises(OwnerGuardIsolationError, match=r"^owner_guard_shape_invalid$"):
        validate(source)


@pytest.mark.parametrize("task_case", ("query_context", "source_citation", "new_task"))
def test_no_extra_task_slot(task_case: str) -> None:
    with pytest.raises(OwnerGuardIsolationError, match=r"^owner_guard_shape_invalid$"):
        validate(synthetic_source(task_case))


def authorized_forget(source: dict[str, Any], *, change_audit: bool) -> None:
    source["write_observation"]["write_performed"] = True
    source["write_observation"]["authorized_mutation"] = {
        "observed": True,
        "operation": "owner_forget",
        "owner_authorized": True,
        "receipt_sha256": digest("authorized-forget-receipt"),
    }
    if change_audit:
        source["write_observation"]["audit_head_after_sha256"] = digest("audit-after")


@pytest.mark.parametrize("change_audit", (False, True))
def test_continuity_owner_forget_preserves_existing_authorized_write_semantics(
    change_audit: bool,
) -> None:
    source = synthetic_source()
    authorized_forget(source, change_audit=change_audit)
    assert validate(source)["formal_admission"] is False


@pytest.mark.parametrize("task_case", ("living_wiki", "professional_evidence"))
def test_other_tasks_cannot_use_owner_forget(task_case: str) -> None:
    source = synthetic_source(task_case)
    authorized_forget(source, change_audit=True)
    with pytest.raises(OwnerGuardIsolationError, match=r"^owner_guard_write_invalid$"):
        validate(source)


@pytest.mark.parametrize("task_case", ("continuity", "living_wiki", "professional_evidence"))
def test_changed_audit_head_requires_observed_authorized_continuity_write(task_case: str) -> None:
    source = synthetic_source(task_case)
    source["write_observation"]["audit_head_after_sha256"] = digest("audit-after")
    with pytest.raises(OwnerGuardIsolationError, match=r"^owner_guard_write_invalid$"):
        validate(source)


@pytest.mark.parametrize(
    "field,value", (("operation", None), ("owner_authorized", False), ("receipt_sha256", None))
)
def test_observed_mutation_requires_complete_owner_authorization(field: str, value: Any) -> None:
    source = synthetic_source()
    authorized_forget(source, change_audit=True)
    source["write_observation"]["authorized_mutation"][field] = value
    with pytest.raises(OwnerGuardIsolationError, match=r"^owner_guard_write_invalid$"):
        validate(source)


@pytest.mark.parametrize(
    "field,value",
    (
        ("operation", "owner_forget"),
        ("owner_authorized", True),
        ("receipt_sha256", digest("unobserved-receipt")),
    ),
)
def test_unobserved_mutation_cannot_have_authorization_fields(field: str, value: Any) -> None:
    source = synthetic_source()
    source["write_observation"]["authorized_mutation"][field] = value
    with pytest.raises(OwnerGuardIsolationError, match=r"^owner_guard_write_invalid$"):
        validate(source)


@pytest.mark.parametrize("authorized", (False, True))
def test_write_performed_must_equal_authorized_mutation_observation(authorized: bool) -> None:
    source = synthetic_source()
    if authorized:
        authorized_forget(source, change_audit=False)
    source["write_observation"]["write_performed"] = not authorized
    with pytest.raises(OwnerGuardIsolationError, match=r"^owner_guard_write_invalid$"):
        validate(source)


def test_old_v1_and_new_role_bound_source_are_distinct_closed_contracts() -> None:
    source = synthetic_source()
    new_schema = json.loads((CONTRACTS / SCHEMA_FILENAME).read_text())
    old_schema = json.loads((CONTRACTS / "v013-host-task-evidence.v1.schema.json").read_text())
    Draft202012Validator.check_schema(new_schema)
    old = {field: copy.deepcopy(source[field]) for field in ENVELOPE_FIELDS}
    old.update(
        {
            "schema_version": "deeplaw.v013-host-task-evidence/v1",
            "artifact_kind": "isolation_receipt",
            "process_boundary": copy.deepcopy(source["process_boundary"]),
            "write_observation": copy.deepcopy(source["write_observation"]),
            "claim_eligible": False,
            "secret_boundary": {
                "parent_secret_present": True,
                "child_secret_present": False,
                "auth_read": False,
                "transcript_read": False,
                "prompt_read": False,
                "reasoning_read": False,
                "secret_read": False,
            },
        }
    )
    assert Draft202012Validator(old_schema).is_valid(old)
    assert not Draft202012Validator(old_schema).is_valid(source)
    assert Draft202012Validator(new_schema).is_valid(source)
    assert not Draft202012Validator(new_schema).is_valid(old)
    with pytest.raises(OwnerGuardIsolationError, match=r"^owner_guard_shape_invalid$"):
        validate_owner_guard_isolation(
            old, envelope=envelope_for(source), expected_bindings=bindings_for(source)
        )


def test_old_secret_boundary_cannot_be_relabelled_into_new_source() -> None:
    source = synthetic_source()
    source["secret_boundary"] = {"parent_secret_present": False}
    with pytest.raises(OwnerGuardIsolationError, match=r"^owner_guard_shape_invalid$"):
        validate(source)


def test_invalid_payload_values_are_not_echoed() -> None:
    source = synthetic_source()
    source["actors"]["host"]["source_sha256"] = "/private/PUBLIC_SYNTHETIC_CANARY"
    with pytest.raises(OwnerGuardIsolationError) as error:
        validate(source)
    assert str(error.value) == "owner_guard_shape_invalid"
    assert "CANARY" not in str(error.value)
    assert error.value.__cause__ is None


REQUIRED_OBSERVATION_PATHS = (
    *(("actors", role) for role in ACTOR_ROLES),
    *(
        ("actors", role, field)
        for role in ACTOR_ROLES
        for field in ("process_identity_sha256", "source_sha256", "instance_sha256")
    ),
    *(("credential_observation", "key_received", role) for role in ACTOR_ROLES[1:]),
    *(("inspection_observation", "store_reads", role) for role in ACTOR_ROLES),
    ("credential_observation", "mode"),
    ("credential_observation", "authority_key_present"),
    ("inspection_observation", "authority_public_request_inspected"),
    ("inspection_observation", "observer_public_native_response_inspected"),
    ("inspection_observation", "raw_request_retained"),
    ("inspection_observation", "raw_native_response_retained"),
)


@pytest.mark.parametrize("path", REQUIRED_OBSERVATION_PATHS)
@pytest.mark.parametrize("change", ("missing", "none"))
def test_required_role_and_inspection_fields_cannot_be_missing_or_none(
    path: tuple[str, ...], change: str
) -> None:
    source = synthetic_source()
    if change == "missing":
        del node(source, path[:-1])[path[-1]]
    else:
        node(source, path[:-1])[path[-1]] = None
    with pytest.raises(OwnerGuardIsolationError, match=r"^owner_guard_shape_invalid$"):
        validate(source)


@pytest.mark.parametrize("field", ENVELOPE_FIELDS)
def test_existing_envelope_requires_every_binding(field: str) -> None:
    source = synthetic_source()
    envelope = envelope_for(source)
    del envelope[field]
    with pytest.raises(OwnerGuardIsolationError, match=r"^owner_guard_envelope_invalid$"):
        validate_owner_guard_isolation(
            source, envelope=envelope, expected_bindings=bindings_for(source)
        )


@pytest.mark.parametrize("field", ("mode", "schema_version", "artifact_kind"))
def test_fixed_source_mode_and_identity_cannot_be_relabelled(field: str) -> None:
    source = synthetic_source()
    if field == "mode":
        source["credential_observation"][field] = "direct_host_key_delivery"
    else:
        source[field] = "unsupported_source"
    with pytest.raises(OwnerGuardIsolationError, match=r"^owner_guard_shape_invalid$"):
        validate(source)


def test_validator_does_not_accept_a_formal_admission_field() -> None:
    source = synthetic_source()
    source["formal_admission"] = True
    with pytest.raises(OwnerGuardIsolationError, match=r"^owner_guard_shape_invalid$"):
        validate(source)


def test_missing_schema_error_does_not_include_a_local_path(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def unavailable(_path: Path, **_kwargs: Any) -> str:
        raise OSError("/private/PUBLIC_SYNTHETIC_CANARY")

    monkeypatch.setattr(Path, "read_text", unavailable)
    with pytest.raises(OwnerGuardIsolationError) as error:
        validate(synthetic_source())
    assert str(error.value) == "owner_guard_schema_unavailable"
    assert error.value.__cause__ is None


@pytest.mark.parametrize("kind", ("cycle", "huge_text", "nonfinite", "array"))
def test_untyped_inputs_are_rejected_without_payload_diagnostics(kind: str) -> None:
    source = synthetic_source()
    if kind == "cycle":
        source["raw_payload"] = source
    elif kind == "huge_text":
        source["raw_payload"] = "PUBLIC_SYNTHETIC_CANARY" * 1000
    elif kind == "nonfinite":
        source["run_binding"]["workflow_run_id"] = float("nan")
    else:
        source["raw_payload"] = ["PUBLIC_SYNTHETIC_CANARY"]
    with pytest.raises(OwnerGuardIsolationError) as error:
        validate(source)
    assert str(error.value) == "owner_guard_shape_invalid"
    assert error.value.__cause__ is None


DIGEST_PATHS = (
    *(
        ("actors", role, field)
        for role in ACTOR_ROLES
        for field in ("process_identity_sha256", "source_sha256", "instance_sha256")
    ),
    *((field,) for field in BINDING_FIELDS),
    ("candidate_binding", "commit"),
    ("candidate_binding", "tree"),
    ("candidate_binding", "lock_sha256"),
    ("candidate_binding", "wheel_sha256"),
    ("candidate_binding", "sdist_sha256"),
    ("corpus", "sha256"),
    ("runner", "sha256"),
    ("scorer", "sha256"),
    ("write_observation", "audit_head_before_sha256"),
    ("write_observation", "audit_head_after_sha256"),
)


@pytest.mark.parametrize("path", DIGEST_PATHS)
def test_digest_and_git_metadata_cannot_have_a_trailing_newline(path: tuple[str, ...]) -> None:
    source = synthetic_source()
    node(source, path[:-1])[path[-1]] += "\n"
    with pytest.raises(OwnerGuardIsolationError, match=r"^owner_guard_shape_invalid$"):
        validate(source)


@pytest.mark.parametrize(
    "path", (("runner", "identity"), ("scorer", "identity"), ("run_binding", "run_id"))
)
def test_identifiers_keep_existing_consumer_full_match_semantics(path: tuple[str, str]) -> None:
    source = synthetic_source()
    node(source, path[:-1])[path[-1]] += "\n"
    with pytest.raises(OwnerGuardIsolationError, match=r"^owner_guard_shape_invalid$"):
        validate(source)


def test_authorized_mutation_receipt_digest_cannot_have_a_trailing_newline() -> None:
    source = synthetic_source()
    authorized_forget(source, change_audit=True)
    source["write_observation"]["authorized_mutation"]["receipt_sha256"] += "\n"
    with pytest.raises(OwnerGuardIsolationError, match=r"^owner_guard_shape_invalid$"):
        validate(source)
