from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from benchmarks.hosts import pass13_evidence
from benchmarks.hosts import run_pass13_codex_continuity_qualification as qualification


def _isolation_receipt() -> dict[str, object]:
    return {
        "profile_kind": "temporary_closed",
        "home_isolated": True,
        "codex_home_isolated": True,
        "xdg_config_home_isolated": True,
        "xdg_data_home_isolated": True,
        "ambient_host_state_inherited": False,
        "ambient_plugins_inherited": False,
        "ambient_apps_inherited": False,
        "ambient_hooks_inherited": False,
        "secret_values_retained": False,
        "auth_class": "chatgpt_login",
    }


def test_pass13_runner_uses_a_closed_temporary_host_profile(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    ambient = tmp_path / "ambient"
    for name, value in {
        "HOME": ambient / "home",
        "CODEX_HOME": ambient / "codex",
        "XDG_CONFIG_HOME": ambient / "config",
        "XDG_DATA_HOME": ambient / "data",
    }.items():
        monkeypatch.setenv(name, str(value))
    monkeypatch.setenv("OPENAI_API_KEY", "pass14-forbidden-openai-key")
    monkeypatch.setenv("DEEPLAW_UNRELATED_PLUGIN_STATE", "enabled")

    profile = tmp_path / "profile"
    environment = qualification._host_environment(
        Path("/opt/codex"),
        profile,
        {"DEEPLAW_QUALIFICATION_SECRET_CANARY": "canary"},
    )

    expected_roots = {
        "HOME": profile / "home",
        "CODEX_HOME": profile / "codex",
        "XDG_CONFIG_HOME": profile / "xdg-config",
        "XDG_DATA_HOME": profile / "xdg-data",
    }
    for name, expected in expected_roots.items():
        assert Path(environment[name]) == expected
        assert expected.is_dir()
        assert environment[name] != os.environ[name]
    assert "OPENAI_API_KEY" not in environment
    assert "DEEPLAW_UNRELATED_PLUGIN_STATE" not in environment

    receipt = qualification._isolation_receipt(profile, environment)
    assert receipt == _isolation_receipt()
    assert str(tmp_path) not in json.dumps(receipt, sort_keys=True)


def test_app_server_argv_is_read_only_and_exposes_one_mcp_tool(tmp_path: Path) -> None:
    codex_binary = Path("/opt/codex")
    argv = qualification._app_server_argv(
        codex_binary,
        mcp_wrapper=tmp_path / "deeplaw-mcp",
    )
    assert argv[:3] == [str(codex_binary), "app-server", "--stdio"]
    rendered = " ".join(argv)
    assert 'approval_policy="never"' in rendered
    assert 'model="gpt-5.6-luna"' in rendered
    assert 'model_reasoning_effort="max"' in rendered
    assert 'web_search="disabled"' in rendered
    assert 'mcp_servers.deeplaw.enabled_tools=["knowledge_support"]' in rendered
    assert "mcp_servers.deeplaw.command=" in rendered
    assert "mcp_servers={}" in rendered


def test_ambient_server_is_explicitly_disabled_and_nonempty_status_fails() -> None:
    argv = qualification._app_server_argv(
        Path("/opt/codex"),
        mcp_wrapper=Path("deeplaw-mcp"),
        ambient_servers=("node_repl", "openaiDeveloperDocs"),
    )
    assert "mcp_servers.node_repl.enabled=false" in argv
    assert "mcp_servers.openaiDeveloperDocs.enabled=false" in argv
    assert not qualification._mcp_status_valid(
        {
            "data": [
                {"name": "deeplaw", "tools": {"knowledge_support": {}}},
                {"name": "node_repl", "tools": {"exec": {}}},
            ],
            "nextCursor": None,
        }
    )


def test_ambient_inventory_map_names_are_all_disabled_with_safe_keys() -> None:
    inventory = {
        "servers": {
            "deeplaw": {"enabled": True},
            "node_repl": {"enabled": True},
            "openaiDeveloperDocs": {"enabled": True},
        }
    }
    names = qualification._configured_mcp_server_names(inventory)
    assert names == ["deeplaw", "node_repl", "openaiDeveloperDocs"]
    argv = qualification._app_server_argv(
        Path("/opt/codex"),
        mcp_wrapper=Path("deeplaw-mcp"),
        ambient_servers=[name for name in names if name != "deeplaw"],
    )
    assert "mcp_servers.node_repl.enabled=false" in argv
    assert "mcp_servers.openaiDeveloperDocs.enabled=false" in argv


def test_codex_login_receipt_hashes_status_without_reading_auth_file(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class Completed:
        returncode = 0
        stdout = b"Logged in using ChatGPT\n"
        stderr = b""

    monkeypatch.setattr(qualification.subprocess, "run", lambda *args, **kwargs: Completed())
    receipt = qualification._codex_authentication_receipt(
        Path("/opt/codex"), {"PATH": "/usr/bin"}
    )
    assert receipt == {
        "checked": True,
        "raw_sha256": qualification._sha256(Completed.stdout),
        "raw_bytes": len(Completed.stdout),
    }


def test_checkpoint_body_has_exact_governed_labels() -> None:
    body = qualification._checkpoint_body(
        "cold_start",
        decision="decision",
        next_action="next",
        verified="verified",
        gap="gap",
        artifact="artifact",
    )
    assert [line.split(":", 1)[0] for line in body.splitlines()] == [
        "GOAL",
        "CONFIRMED_DECISION",
        "CONSTRAINT",
        "VERIFIED_FACT",
        "OPEN_GAP",
        "NEXT_ACTION",
        "ARTIFACT_REF",
    ]


def test_installed_cli_parser_accepts_pretty_printed_public_json() -> None:
    assert qualification._parse_json_output('{\n  "schema_version": "fixture/v1"\n}\n') == {
        "schema_version": "fixture/v1"
    }


def test_seed_vault_uses_owner_mutations_expiry_and_binding_distractors(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    requests: list[dict[str, object]] = []
    remember_count = 0

    def fake_cli(executable: Path, arguments: list[str], *, cwd: Path, environment=None):
        nonlocal remember_count
        if arguments[1:3] == ["sink", "enable"]:
            return {"grant_id": "grant_pass13"}
        if arguments[1:3] == ["sink", "apply"]:
            request_path = Path(arguments[arguments.index("--request") + 1])
            request = json.loads(request_path.read_text(encoding="utf-8"))
            requests.append(request)
            if request["operation"] == "remember":
                remember_count += 1
                return {
                    "knowledge_id": f"knowledge_{remember_count:024x}",
                    "revision_id": f"knowledgerev_{remember_count:024x}",
                }
            return {}
        if arguments[1:3] == ["autonomy", "status"]:
            return {"audit_head": f"{len(requests) + 1:064x}"}
        return {}

    monkeypatch.setattr(qualification, "_run_installed_cli", fake_cli)
    seeded = qualification._seed_vault(
        Path("/opt/deeplaw"),
        tmp_path / "vault",
        {"cold_start": qualification._make_binding("cold_start")},
        work_dir=tmp_path,
    )
    assert seeded["grant_id"] == "grant_pass13"
    record_runs = [request for request in requests if request["operation"] == "record_run"]
    remembers = [request for request in requests if request["operation"] == "remember"]
    assert len(record_runs) == 4
    assert len(remembers) == 5
    assert all(request["memory_type"] == "working" for request in remembers)
    assert all(request["expires_at"] == "2099-01-01T00:00:00Z" for request in remembers)
    assert all(
        [line.split(":", 1)[0] for line in request["body"].splitlines()]
        == [
            "GOAL",
            "CONFIRMED_DECISION",
            "CONSTRAINT",
            "VERIFIED_FACT",
            "OPEN_GAP",
            "NEXT_ACTION",
            "ARTIFACT_REF",
        ]
        for request in remembers
    )
    assert all("task_binding" in request.get("run_metadata", {}) for request in record_runs)


def test_report_builder_is_schema_bound_and_claim_false(tmp_path: Path) -> None:
    from deeplaw.knowledge_mcp_server import knowledge_tool_definition

    orchestrator = qualification.QualificationOrchestrator(
        host="codex",
        repository=qualification._repository(),
        candidate_wheel=tmp_path / "candidate.whl",
        deeplaw_executable=tmp_path / "deeplaw",
        output_dir=tmp_path / "evidence",
        error_type=qualification.QualificationFailure,
    )
    report = orchestrator.build_report(
        binding={
            "commit": "a" * 40,
            "tree": "b" * 40,
            "worktree_clean": True,
            "wheel_name": "deeplaw-0.12.0-py3-none-any.whl",
            "wheel_sha256": "c" * 64,
            "wheel_bytes": 100,
            "runtime_executable_sha256": "d" * 64,
            "import_path_class": "isolated_site_packages",
            "contract_digests": {
                "knowledge-support.input.v6.schema.json": "e" * 64,
                "knowledge-support.output.v6.schema.json": "f" * 64,
                "knowledge-sink.input.v2.schema.json": "0" * 64,
            },
        },
        environment={
            "operating_system": "Darwin",
            "architecture": "arm64",
            "python_version": "3.13",
            "isolation": _isolation_receipt(),
        },
        host_attestation={
            **qualification._placeholder_attestation(),
            "version": qualification.CODEX_VERSION,
        },
        tool_schema=pass13_evidence.knowledge_support_tool_schema_receipt(
            [knowledge_tool_definition(autonomous=True)]
        ),
        runs=[
            qualification._placeholder_run(index, scenario)
            for index, scenario in enumerate(("cold_start", "resume_fork", "compaction_forget"), 1)
        ],
        lifecycle={
            "host_owns_threads": True,
            "common_task_families": ["cold_start", "resume_fork", "compaction_forget"],
            "transport_seams": [],
            "requested_operations": [],
            "methods_observed": [],
            "deeplaw_session_store_created": False,
        },
        security=qualification._placeholder_security(),
        not_executed=["OpenCode host"],
    )
    assert report["claim_eligible"] is False
    assert report["release_ready"] is False


def test_scenario_driver_uses_client_lifecycle_and_rejects_three_calls(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[str] = []

    class FakeResult(dict):
        @property
        def tool_outputs(self):
            return []

        @property
        def tool_call_observations(self):
            return []

        @property
        def events(self):
            return []

        @property
        def usage(self):
            return {
                key: "unreported"
                for key in (
                    "input_tokens",
                    "cached_input_tokens",
                    "cache_write_input_tokens",
                    "output_tokens",
                    "reasoning_output_tokens",
                    "total_tokens",
                )
            }

    turn_params: list[dict[str, object]] = []

    class FakeClient:
        def initialize(self):
            calls.append("initialize")

        def thread_start(self, *args, **kwargs):
            calls.append("thread/start")
            return {"thread": {"id": "t1"}}

        def turn_start(self, *args, **kwargs):
            calls.append("turn/start")
            turn_params.append(kwargs["params"])
            return FakeResult(thread_id="t1", turn_id="u1", final_text="{}")

        def thread_resume(self, *args, **kwargs):
            calls.append("thread/resume")
            return {"thread": {"id": "t1"}}

        def thread_fork(self, *args, **kwargs):
            calls.append("thread/fork")
            return {"thread": {"id": "t2"}}

        def thread_compact_start(self, *args, **kwargs):
            calls.append("thread/compact/start")
            return {"status": "started"}

        def close(self):
            calls.append("close")

    monkeypatch.setattr(qualification, "CodexAppServerClient", FakeClient)
    with pytest.raises(qualification.QualificationFailure, match="safe read"):
        qualification._run_scenario(
            client=FakeClient(),
            scenario="cold_start",
            task_binding={"binding_sha256": "a" * 64},
            prompt="qualification",
            ledger_head=lambda: "b" * 64,
            forget_checkpoint=None,
            expectations={
                "seed_boundary": {
                    "kind": "seed_checkpoint",
                    "owner_enabled": True,
                    "read_mcp_write_performed": False,
                    "audit_changed": True,
                    "audit_head_before": "1" * 64,
                    "audit_head_after": "2" * 64,
                    "receipt_sha256": "3" * 64,
                    "target_sha256": "4" * 64,
                }
            },
        )
    assert turn_params == [{"outputSchema": qualification._FINAL_RESPONSE_SCHEMA}]


def test_codex_failure_codes_are_safe_constant_labels() -> None:
    assert qualification._safe_failure_code(
        qualification.QualificationFailure(
            "bounded final response schema was not satisfied"
        )
    ) == "final_response_schema_invalid"
    assert qualification._safe_failure_code(
        qualification.QualificationFailure("provider included unsafe arbitrary text")
    ) == "host_qualification_failure"


def test_turn_record_rejects_failed_turn_prohibited_capability_and_path(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    binding = qualification._make_binding("cold_start")
    binding_sha256 = qualification._sha256(
        qualification.canonical_json(binding).encode("utf-8")
    )
    monkeypatch.setattr(
        qualification,
        "analyze_safe_read_calls",
        lambda observations, outputs: {
            "call_count": 1,
            "first_call_valid": True,
            "bounded_retry_used": False,
            "safe_read_operations": ["context"],
            "provider_payloads": [{"write_performed": False, "gap_count": 0}],
        },
    )
    monkeypatch.setattr(
        qualification,
        "bind_relevant_chars",
        lambda safe_read, outputs, relevant_text: safe_read,
    )
    result = {
        "status": "failed",
        "thread_id": "thread-1",
        "turn_id": "turn-1",
        "final_text": json.dumps(
            {
                "summary": "bounded",
                "next_step": "next",
                "preserved_decisions": [],
                "open_gaps": [],
            }
        ),
        "tool_call_observations": [
            {
                "server": "deeplaw",
                "tool_name": "knowledge_support",
                "status": "completed",
                "argument_operation": "context",
                "argument_task_present": True,
                "argument_confirm_no_case_data": True,
                "argument_query_plan_version": "6",
                "argument_task_binding_sha256": binding_sha256,
            }
        ],
        "tool_outputs": [{}],
        "usage": {
            "input_tokens": 10,
            "cached_input_tokens": 2,
            "cache_write_input_tokens": 0,
            "output_tokens": 5,
            "reasoning_output_tokens": 1,
            "total_tokens": 15,
        },
        "events": [],
    }
    kwargs = {
        "lifecycle_method": "thread/start",
        "prompt": "fixture",
        "ledger_before": "a" * 64,
        "ledger_after": "a" * 64,
        "expected_task_binding": binding,
    }
    with pytest.raises(qualification.QualificationFailure, match="did not complete"):
        qualification._turn_record(result, **kwargs)

    result["status"] = "completed"
    observation = result["tool_call_observations"][0]
    for required_field in ("argument_task_present", "argument_query_plan_version"):
        retained = observation.pop(required_field)
        with pytest.raises(qualification.QualificationFailure, match="did not bind"):
            qualification._turn_record(result, **kwargs)
        observation[required_field] = retained

    retained_binding = observation.pop("argument_task_binding_sha256")
    unbound_record, _ = qualification._turn_record(
        result,
        **kwargs,
        require_task_binding=False,
    )
    assert unbound_record["status"] == "passed"
    observation["argument_task_binding_sha256"] = retained_binding

    result["events"] = [
        {
            "method": "mcpServer/startupStatus/updated",
            "item_status": "ready",
            "server_name": "deeplaw",
        }
    ]
    record, _ = qualification._turn_record(result, **kwargs)
    assert record["status"] == "passed"

    result["events"] = [{"method": "web_search", "item_type": "webSearch"}]
    with pytest.raises(qualification.QualificationFailure, match="prohibited capability"):
        qualification._turn_record(result, **kwargs)

    result["events"] = [
        {"method": "item/agentMessage/delta", "item_type": "disallowed"},
        {"method": "item/reasoning/delta", "item_type": "disallowed"},
    ]
    record, _ = qualification._turn_record(result, **kwargs)
    assert record["status"] == "passed"

    result["usage"] = qualification._empty_usage()
    with pytest.raises(qualification.QualificationFailure, match="actual Codex provider"):
        qualification._turn_record(result, **kwargs)
    result["usage"] = {
        "input_tokens": 10,
        "cached_input_tokens": 2,
        "cache_write_input_tokens": 0,
        "output_tokens": 5,
        "reasoning_output_tokens": 1,
        "total_tokens": 15,
    }

    result["events"] = []
    result["final_text"] = json.dumps(
        {
            "summary": "/tmp/provider-secret",
            "next_step": "next",
            "preserved_decisions": [],
            "open_gaps": [],
        }
    )
    with pytest.raises(qualification.QualificationFailure, match="prohibited data"):
        qualification._turn_record(result, **kwargs)


def test_artifact_writer_is_used_before_bundle_manifest(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    written: list[str] = []
    monkeypatch.setattr(
        qualification,
        "write_retained_artifact",
        lambda path, data, **kwargs: (
            written.append(path.name) or {"name": path.name, "bytes": len(data), "sha256": "a" * 64}
        ),
    )
    qualification._write_artifacts(
        tmp_path,
        {"report.json": b'{"status":"failed"}\n'},
        forbidden_values=("secret",),
    )
    assert written == ["report.json"]
