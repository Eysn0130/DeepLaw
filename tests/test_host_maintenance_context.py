"""Actual governed revisions feed the finite Host context fixture."""
from copy import deepcopy

import pytest

from benchmarks.hosts import maintenance_host_context, maintenance_task_mcp
from benchmarks.hosts.maintenance_host_context import (
    capture_context,
    digest,
    prepare_context_vault,
    record_host_outcome,
)
from benchmarks.hosts.maintenance_task_cases import MaintenanceTaskSession, score_host_trace
from deeplaw.knowledge_autonomy import AutonomousKnowledgeStore
from deeplaw.util import canonical_json, sha256_bytes


def _persisted_trace(
    tmp_path,
    context,
    *,
    configuration: str,
    scenario: str,
    run_id: str,
    candidate_id: str,
    action: dict[str, object],
) -> dict[str, object]:
    session = MaintenanceTaskSession(configuration, scenario)
    session.submit(action)
    binding = maintenance_task_mcp.make_owner_binding(
        configuration,
        scenario,
        run_id=run_id,
        candidate_id=candidate_id,
        context_id=context["context_id"],
        capsule_digest=maintenance_task_mcp.provider_capsule_digest(
            context["provider_capsule"]
        ),
    )
    trace_path = tmp_path / f"{run_id}.json"
    trace_store = maintenance_task_mcp._TraceStore(trace_path, binding, session)
    trace_store.persist(session)
    return maintenance_task_mcp.load_persisted_trace(trace_path, binding=binding)


def _resource_action(
    state_sha256: str,
    *,
    action_id: str = "source-update-1",
    observed_state_sha256: str | None = None,
    experience_id: str | None = None,
) -> dict[str, object]:
    parameters: dict[str, object] = {
        "resource_id": "orchid-archive",
        "version": "v2",
        "source_ref": "source:orchid-v2",
    }
    if experience_id is not None:
        parameters["experience_id"] = experience_id
    return {
        "action_id": action_id,
        "observed_state_sha256": observed_state_sha256 or state_sha256,
        "kind": "submit_resource_version",
        "parameters": parameters,
    }


@pytest.fixture
def outcome_inputs(tmp_path):
    vault = tmp_path / "vault"
    setup = prepare_context_vault(vault, "governed_maintenance")
    context = capture_context(vault, "governed_maintenance", "source_update")
    session = MaintenanceTaskSession("governed_maintenance", "source_update")
    trace = _persisted_trace(
        tmp_path, context, configuration="governed_maintenance", scenario="source_update",
        run_id="offline-validation-test", candidate_id="offline-validation-candidate",
        action=_resource_action(session.state_sha256, experience_id="experience:governed-v2"),
    )
    return vault, setup, {
        "configuration": "governed_maintenance", "scenario": "source_update",
        "host_run_id": "offline-validation-test", "candidate_id": "offline-validation-candidate",
        "context": context, "trace_payload": trace,
        "score": score_host_trace("governed_maintenance", "source_update", trace["events"]),
    }


def _vault_snapshot(vault):
    return {path.relative_to(vault).as_posix(): sha256_bytes(path.read_bytes())
            for path in vault.rglob("*") if path.is_file()}


def _reject_sink_write(*args, **kwargs):
    raise AssertionError("read-only validation entered the mutation sink")


def test_offline_outcome_validation_is_read_only_and_requires_actual_vault(
    outcome_inputs, monkeypatch,
):
    vault, setup, inputs = outcome_inputs
    before = _vault_snapshot(vault)
    monkeypatch.setattr(maintenance_host_context, "handle_knowledge_sink", _reject_sink_write)
    validated = maintenance_host_context.validate_host_outcome(vault, **inputs)
    assert validated == inputs["trace_payload"]
    assert validated is not inputs["trace_payload"]
    rendered = canonical_json(validated)
    assert setup["grant_id"] not in rendered
    assert str(vault) not in rendered
    assert _vault_snapshot(vault) == before

    missing = vault.parent / "missing-vault"
    with pytest.raises((OSError, RuntimeError, ValueError)):
        maintenance_host_context.validate_host_outcome(missing, **inputs)
    assert not missing.exists()
    assert _vault_snapshot(vault) == before


@pytest.mark.parametrize("tampering", [
    "trace", "candidate", "task", "provider", "capsule", "score",
])
def test_offline_outcome_validation_rejects_tampering_without_writes(
    outcome_inputs, monkeypatch, tampering,
):
    vault, _, original = outcome_inputs
    inputs = deepcopy(original)
    if tampering == "trace":
        inputs["trace_payload"]["events"][0]["state_sha256"] = "0" * 64
    elif tampering == "candidate":
        inputs["candidate_id"] = "another-candidate"
    elif tampering == "task":
        inputs["trace_payload"]["task"]["goal"] = "A different public goal."
    elif tampering == "provider":
        inputs["context"]["provider_capsule"] = {"schema_version": "tampered"}
    elif tampering == "capsule":
        inputs["context"]["capsule"]["capsule_digest"] = "0" * 64
    else:
        inputs["score"]["passed"] = False
    before = _vault_snapshot(vault)
    monkeypatch.setattr(maintenance_host_context, "handle_knowledge_sink", _reject_sink_write)
    with pytest.raises(ValueError):
        maintenance_host_context.validate_host_outcome(vault, **inputs)
    assert _vault_snapshot(vault) == before


def test_outcome_recorder_calls_shared_validator_before_mutation(outcome_inputs, monkeypatch):
    vault, setup, inputs = outcome_inputs
    calls = []

    def reject(path, **kwargs):
        calls.append((path, kwargs))
        raise ValueError("shared validation sentinel")

    before = _vault_snapshot(vault)
    monkeypatch.setattr(maintenance_host_context, "validate_host_outcome", reject)
    monkeypatch.setattr(maintenance_host_context, "handle_knowledge_sink", _reject_sink_write)
    with pytest.raises(ValueError, match="shared validation sentinel"):
        record_host_outcome(vault, setup["grant_id"], **inputs,
                            host_id="synthetic-host", model_id="synthetic-test")
    assert calls == [(vault, inputs)]
    assert _vault_snapshot(vault) == before


def test_fixture_capture_and_outcome_leave_no_active_capability(outcome_inputs):
    vault, setup, inputs = outcome_inputs
    assert setup["grant_status"] == "revoked_after_fixture_setup"
    maintenance_host_context.assert_context_grants_closed(vault)
    outcome = record_host_outcome(
        vault, setup["grant_id"], **inputs, host_id="synthetic-host", model_id="synthetic-test",
    )
    assert outcome["run_record"]["run_id"]
    maintenance_host_context.assert_context_grants_closed(vault)
    with AutonomousKnowledgeStore(vault, read_only=True) as store:
        rows = store.connection.execute(
            "SELECT operations_json, revoked_at FROM knowledge_sink_grants_v3 ORDER BY created_at"
        ).fetchall()
        assert store.vault_id == setup["vault_id"]
    assert len(rows) == 2 and all(row["revoked_at"] is not None for row in rows)
    assert rows[1]["operations_json"] == canonical_json(["record_feedback", "record_run"])
    subsequent = capture_context(vault, "governed_maintenance", "wrong_experience")
    assert subsequent["verification"]["valid"] is True
    with pytest.raises(ValueError, match="no longer verifies"):
        maintenance_host_context.validate_host_outcome(vault, **inputs)


def test_failed_outcome_revokes_grant_and_preserves_mutation_failure(outcome_inputs, monkeypatch):
    vault, setup, inputs = outcome_inputs

    def fail(*args, **kwargs):
        raise RuntimeError("public mutation sentinel")

    monkeypatch.setattr(maintenance_host_context, "handle_knowledge_sink", fail)
    with pytest.raises(RuntimeError, match="public mutation sentinel"):
        record_host_outcome(vault, setup["grant_id"], **inputs,
                            host_id="synthetic-host", model_id="synthetic-test")
    maintenance_host_context.assert_context_grants_closed(vault)
    assert capture_context(vault, "governed_maintenance", "wrong_experience")[
        "verification"
    ]["valid"] is True


def test_outcome_cleanup_failure_blocks_next_capture_and_retains_both_failures(
    outcome_inputs, monkeypatch,
):
    vault, setup, inputs = outcome_inputs

    def fail_mutation(*args, **kwargs):
        raise RuntimeError("public mutation sentinel")

    def fail_cleanup(*args, **kwargs):
        raise OSError("public cleanup sentinel")

    monkeypatch.setattr(maintenance_host_context, "handle_knowledge_sink", fail_mutation)
    monkeypatch.setattr(AutonomousKnowledgeStore, "disable_grant", fail_cleanup)
    with pytest.raises(maintenance_host_context.OutcomeGrantClosureError) as caught:
        record_host_outcome(vault, setup["grant_id"], **inputs,
                            host_id="synthetic-host", model_id="synthetic-test")
    assert caught.value.mutation_failure == "RuntimeError"
    assert caught.value.cleanup_failure == "OSError"
    with pytest.raises(ValueError, match="grant or integrity gap"):
        capture_context(vault, "governed_maintenance", "wrong_experience")


def test_capture_refuses_unexpected_capability_material(outcome_inputs):
    vault, _, _ = outcome_inputs
    (vault / ".deeplaw/capabilities/unexpected.token").write_text("synthetic marker")
    with pytest.raises(ValueError):
        capture_context(vault, "governed_maintenance", "wrong_experience")


@pytest.mark.parametrize("configuration,count", [
    ("no_memory", 0), ("frozen_unmaintained", 2), ("governed_maintenance", 1),
])
def test_verified_context_uses_real_revisions_and_forgetting(tmp_path, configuration, count):
    vault = tmp_path / "vault"
    setup = prepare_context_vault(vault, configuration)
    context = capture_context(vault, configuration, "source_update")
    assert context["verification"]["valid"] is True
    assert len(context["capsule"]["knowledge_revisions"]) == count
    assert setup["memory_provenance"] == "source_free_synthetic_fixture"
    assert setup["source_binding"] == "none"
    assert context["context_provenance"] == "source_free_synthetic_memory_compile"
    rendered = canonical_json(context["provider_capsule"])
    assert "cobalt-before-forget" not in rendered
    assert setup["grant_id"] not in rendered
    if configuration == "frozen_unmaintained":
        assert "no independent record is needed" in rendered
    if configuration == "governed_maintenance":
        assert "Governed v2 source record" in rendered
        assert "no independent record is needed" not in rendered


def test_actual_action_trace_binds_run_and_rejects_tampered_score(tmp_path):
    vault = tmp_path / "vault"
    setup = prepare_context_vault(vault, "no_memory")
    context = capture_context(vault, "no_memory", "source_update")
    session = MaintenanceTaskSession("no_memory", "source_update")
    trace_payload = _persisted_trace(
        tmp_path,
        context,
        configuration="no_memory",
        scenario="source_update",
        run_id="synthetic-binding-test",
        candidate_id="synthetic-candidate",
        action=_resource_action(session.state_sha256, action_id="host-chosen"),
    )
    score = score_host_trace("no_memory", "source_update", trace_payload["events"])
    common = dict(configuration="no_memory", scenario="source_update",
                  host_run_id="synthetic-binding-test", host_id="synthetic-host",
                  model_id="synthetic-test", context=context,
                  trace_payload=trace_payload, candidate_id="synthetic-candidate")
    bad = deepcopy(score)
    bad["passed"] = False
    with pytest.raises(ValueError, match="score differs"):
        record_host_outcome(vault, setup["grant_id"], **common, score=bad)
    receipt = record_host_outcome(vault, setup["grant_id"], **common, score=score)
    assert receipt["run_record"]["input_sha256"] == context["capsule"]["capsule_digest"]
    assert receipt["trace_sha256"] == digest(trace_payload)
    assert receipt["action_state_ledger"] == "not_recorded_by_posthoc_task_run"
    assert receipt["feedback"] == "not_recorded_without_passing_case_or_successful_memory_use"


def test_explicit_observed_memory_use_gets_external_check_feedback(tmp_path):
    vault = tmp_path / "vault"
    setup = prepare_context_vault(vault, "governed_maintenance")
    context = capture_context(vault, "governed_maintenance", "source_update")
    session = MaintenanceTaskSession("governed_maintenance", "source_update")
    action = _resource_action(
        session.state_sha256,
        action_id="host-reference",
        experience_id="experience:governed-v2",
    )
    trace_payload = _persisted_trace(
        tmp_path,
        context,
        configuration="governed_maintenance",
        scenario="source_update",
        run_id="synthetic-feedback-test",
        candidate_id="synthetic-feedback-candidate",
        action=action,
    )
    score = score_host_trace("governed_maintenance", "source_update", trace_payload["events"])
    receipt = record_host_outcome(
        vault, setup["grant_id"], configuration="governed_maintenance", scenario="source_update",
        host_run_id="synthetic-feedback-test", host_id="synthetic-host", model_id="synthetic-test",
        context=context, trace_payload=trace_payload,
        candidate_id="synthetic-feedback-candidate", score=score,
    )
    assert len(receipt["feedback_records"]) == 1
    assert receipt["feedback_records"][0]["evaluator_type"] == "external_check"
    assert receipt["feedback_records"][0]["task_success_authority"] == "external_evidence"


def test_outcome_rejects_wrong_binding_task_and_provider_projection(tmp_path):
    vault = tmp_path / "vault"
    setup = prepare_context_vault(vault, "no_memory")
    context = capture_context(vault, "no_memory", "source_update")
    trace_payload = _persisted_trace(
        tmp_path,
        context,
        configuration="no_memory",
        scenario="source_update",
        run_id="binding-rejection-test",
        candidate_id="candidate-correct",
        action=_resource_action("0" * 64),
    )
    score = score_host_trace("no_memory", "source_update", trace_payload["events"])
    common = dict(
        configuration="no_memory",
        scenario="source_update",
        host_run_id="binding-rejection-test",
        host_id="synthetic-host",
        model_id="synthetic-test",
        context=context,
        trace_payload=trace_payload,
        score=score,
    )
    with pytest.raises(ValueError, match="binding differs"):
        record_host_outcome(
            vault,
            setup["grant_id"],
            **common,
            candidate_id="candidate-wrong",
        )

    tampered_task = deepcopy(trace_payload)
    tampered_task["task"]["goal"] = "A different task goal."
    tampered_task_common = {**common, "trace_payload": tampered_task}
    with pytest.raises(ValueError, match="task binding differs"):
        record_host_outcome(
            vault,
            setup["grant_id"],
            **tampered_task_common,
            candidate_id="candidate-correct",
        )

    tampered_context = deepcopy(context)
    tampered_context["provider_capsule"] = {"schema_version": "tampered"}
    tampered_context_common = {**common, "context": tampered_context}
    with pytest.raises(ValueError, match="provider projection"):
        record_host_outcome(
            vault,
            setup["grant_id"],
            **tampered_context_common,
            candidate_id="candidate-correct",
        )


def test_rejected_memory_reference_does_not_create_feedback(tmp_path):
    vault = tmp_path / "vault"
    setup = prepare_context_vault(vault, "governed_maintenance")
    context = capture_context(vault, "governed_maintenance", "source_update")
    session = MaintenanceTaskSession("governed_maintenance", "source_update")
    trace_payload = _persisted_trace(
        tmp_path,
        context,
        configuration="governed_maintenance",
        scenario="source_update",
        run_id="rejected-memory-test",
        candidate_id="rejected-memory-candidate",
        action=_resource_action(
            session.state_sha256,
            observed_state_sha256="0" * 64,
            experience_id="experience:governed-v2",
        ),
    )
    score = score_host_trace("governed_maintenance", "source_update", trace_payload["events"])
    assert score["passed"] is False
    receipt = record_host_outcome(
        vault,
        setup["grant_id"],
        configuration="governed_maintenance",
        scenario="source_update",
        host_run_id="rejected-memory-test",
        host_id="synthetic-host",
        model_id="synthetic-test",
        context=context,
        trace_payload=trace_payload,
        candidate_id="rejected-memory-candidate",
        score=score,
    )
    assert receipt["run_record"]["status"] == "partial"
    assert receipt["feedback_records"] == []
    assert receipt["feedback"] == "not_recorded_without_passing_case_or_successful_memory_use"
