"""Actual offline MCP journey for synthetic Host preparation; no Provider."""

import os
import sys
from pathlib import Path

import pytest

from benchmarks.hosts import opencode_single_task_producer as producer
from benchmarks.hosts.supervised_procedure_fixture import seed_procedure
from deeplaw.knowledge_autonomy import AutonomousKnowledgeStore, initialize_autonomous_core
from deeplaw.knowledge_store import initialize_knowledge_vault


@pytest.mark.parametrize("compiled", [False, True])
def test_supervised_preflight_requires_queryable_seed(tmp_path, compiled):
    vault = tmp_path / "vault"
    initialize_knowledge_vault(vault, name="supervised-development", scope="project")
    initialize_autonomous_core(vault)
    checkpoint = {"decision": "Keep the reviewed implementation.",
                  "next_action": "Run the bounded regression check."}
    if compiled:
        seed_procedure(vault, checkpoint)
    else:
        # Preserve the actual old fixture as a negative case: active remember
        # alone is not enough for the default v6 Statement discovery contract.
        with AutonomousKnowledgeStore(vault, read_only=False) as store:
            grant = store.enable_grant(writer_id="supervised-development")
            store.remember(
                grant_id=grant["grant_id"], idempotency_key="procedure",
                title="Supervised continuity procedure",
                body=producer.canonical_json(checkpoint), kind="procedure",
                scope="project", sensitivity="public", confirm_no_case_data=True,
            )
    with AutonomousKnowledgeStore(vault, read_only=True) as store:
        before = (store.audit_head, store.legacy_audit_head)
    executable = "deeplaw.exe" if os.name == "nt" else "deeplaw"
    config = {"vault": str(vault), "deeplaw": str(Path(sys.executable).parent / executable)}
    if compiled:
        result = producer.preflight_reads(config, expected_checkpoint=checkpoint)
        assert result["model_invoked"] is False
        assert result["formal_admission"] is False
        assert result["read"]["response_target"] in result["discovery"]["targets"]
    else:
        # TaskGroup propagation retains the original fail-closed reason.
        with pytest.raises(BaseExceptionGroup) as failure:
            producer.preflight_reads(config, expected_checkpoint=checkpoint)
        def leaves(error):
            if isinstance(error, BaseExceptionGroup):
                return [leaf for child in error.exceptions for leaf in leaves(child)]
            return [error]
        assert any(isinstance(item, producer.ProducerError)
                   and str(item) == "discovery returned no exact knowledge reference"
                   for item in leaves(failure.value))
    with AutonomousKnowledgeStore(vault, read_only=True) as store:
        assert (store.audit_head, store.legacy_audit_head) == before


def test_failed_preflight_prevents_guard_and_host_start(tmp_path, monkeypatch):
    from benchmarks.hosts import run_pass13_opencode_continuity_qualification as legacy

    root = tmp_path / "run"
    root.mkdir()
    prefix = {"argv": [], "files": []}
    prepared = {
        "root": str(root), "repository": str(root / "repository"),
        "deeplaw": "unused", "opencode": "unused", "run_id": "synthetic",
        "deployment_sha256": "a" * 64,
        "mcp_launch_prefix_sha256": producer.digest(prefix),
        "host_identity": {"executable_sha256": "b" * 64},
        "case": {"current_checkpoint": {}},
    }
    monkeypatch.setattr(producer, "read_json", lambda path: prepared)
    monkeypatch.setattr(producer, "verify_deployment",
                        lambda: {"source_closure_sha256": "a" * 64})
    monkeypatch.setattr(producer, "validate_runtime_entries", lambda value: None)
    monkeypatch.setattr(producer, "exact_file", lambda path, sha: path)

    def reject(*args, **kwargs):
        raise producer.ProducerError("offline discovery unavailable")

    def forbidden(*args, **kwargs):
        pytest.fail("model infrastructure must not start after failed preflight")

    monkeypatch.setattr(producer, "preflight_reads", reject)
    monkeypatch.setattr(producer, "ExternalGuard", forbidden)
    monkeypatch.setattr(legacy, "_OpenCodeLocalServer", forbidden)
    with pytest.raises(producer.ProducerError, match="offline discovery unavailable"):
        producer.run(root / "prepared.json")
