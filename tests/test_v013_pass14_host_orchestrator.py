from __future__ import annotations

from benchmarks.hosts import pass13_orchestrator
from benchmarks.hosts import run_pass13_codex_continuity_qualification as codex
from benchmarks.hosts import run_pass13_opencode_continuity_qualification as opencode


def test_pass13_hosts_share_one_candidate_and_report_orchestrator() -> None:
    assert codex.QualificationOrchestrator is opencode.QualificationOrchestrator
    assert not hasattr(codex, "_git_binding")
    assert not hasattr(opencode, "_repository_binding")
    assert not hasattr(codex, "_installed_runtime_binding")
    assert not hasattr(opencode, "_installed_binding")
    assert not hasattr(codex, "_build_report")
    assert not hasattr(opencode, "build_skeleton_report")


def test_current_host_runtime_binds_route_and_capsule_contract_bytes() -> None:
    assert "host-session-route-result.v2.schema.json" in (
        pass13_orchestrator.RUNTIME_CONTRACT_NAMES
    )
    assert "host-continuity-capsule.v1.schema.json" in (
        pass13_orchestrator.RUNTIME_CONTRACT_NAMES
    )
