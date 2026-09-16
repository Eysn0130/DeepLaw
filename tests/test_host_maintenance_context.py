"""Actual governed revisions feed the finite Host context fixture."""
from copy import deepcopy

import pytest

from benchmarks.hosts import maintenance_task_mcp
from benchmarks.hosts.maintenance_host_context import (
    capture_context,
    digest,
    prepare_context_vault,
    record_host_outcome,
)
from benchmarks.hosts.maintenance_task_cases import MaintenanceTaskSession, score_host_trace
from deeplaw.util import canonical_json


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
