from __future__ import annotations

import hashlib
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
    monkeypatch.setenv("HTTP_PROXY", "https://user:password@example.invalid")
    monkeypatch.setenv("SSL_CERT_FILE", "/private/credential/cert.pem")

    profile = tmp_path / "profile"
    runtime_executable = tmp_path / "candidate" / "bin" / "deeplaw"
    runtime_executable.parent.mkdir(parents=True)
    environment = qualification._host_environment(
        Path("/opt/codex"),
        profile,
        {"DEEPLAW_QUALIFICATION_SECRET_CANARY": "canary"},
        runtime_executable=runtime_executable,
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
    assert "HTTP_PROXY" not in environment
    assert "SSL_CERT_FILE" not in environment
    assert environment["PATH"].split(os.pathsep)[0] == str(runtime_executable.parent)

    receipt = qualification._isolation_receipt(profile, environment)
    assert receipt == _isolation_receipt()
    assert str(tmp_path) not in json.dumps(receipt, sort_keys=True)


def test_app_server_argv_is_read_only_and_exposes_one_mcp_tool(tmp_path: Path) -> None:
    codex_binary = Path("/opt/codex")
    argv = qualification._app_server_argv(
        codex_binary,
        mcp_wrapper=tmp_path / "deeplaw-mcp",
        codex_launcher=tmp_path / "owner-broker",
    )
    assert argv[:3] == [str(tmp_path / "owner-broker"), "app-server", "--stdio"]
    rendered = " ".join(argv)
    assert 'approval_policy="never"' in rendered
    assert 'model="gpt-5.6-luna"' in rendered
    assert 'model_reasoning_effort="max"' in rendered
    assert 'web_search="disabled"' in rendered
    assert 'mcp_servers.deeplaw.enabled_tools=["knowledge_support"]' in rendered
    assert "mcp_servers.deeplaw.command=" in rendered
    assert "mcp_servers={}" in rendered


def test_qualification_prompt_uses_only_native_host_continuity() -> None:
    prompt = qualification._prompt("cold_start")
    assert "continuity capsule supplied by the native Host context" in prompt
    assert "do not invoke any tool" in prompt
    assert "Call knowledge_support" not in prompt
    assert "task_binding" not in prompt


def test_ambient_server_is_explicitly_disabled_and_nonempty_status_fails() -> None:
    argv = qualification._app_server_argv(
        Path("/opt/codex"),
        mcp_wrapper=Path("deeplaw-mcp"),
        ambient_servers=("node_repl", "openaiDeveloperDocs"),
        codex_launcher=Path("owner-broker"),
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
        codex_launcher=Path("owner-broker"),
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

    calls: list[list[str]] = []

    def run(*args: object, **kwargs: object) -> Completed:
        calls.append(list(args[0]))
        return Completed()

    monkeypatch.setattr(qualification.subprocess, "run", run)
    receipt = qualification._codex_authentication_receipt(
        Path("/opt/owner-broker"), {"PATH": "/usr/bin"}
    )
    assert receipt == {
        "checked": True,
        "raw_sha256": qualification._sha256(Completed.stdout),
        "raw_bytes": len(Completed.stdout),
    }
    assert calls == [[str(Path("/opt/owner-broker")), "login", "status"]]


def test_owner_broker_launcher_is_external_owner_only_and_process_separated(
    tmp_path: Path,
) -> None:
    host = tmp_path / "codex"
    host.write_bytes(b"codex-host")
    host.chmod(0o700)
    launcher = tmp_path / "owner-broker"
    launcher.write_bytes(b"credential-broker")
    launcher.chmod(0o700)
    repository = tmp_path / "repository"
    repository.mkdir()

    assert qualification._validate_owner_broker_launcher(
        launcher,
        host_binary=host,
        repository=repository,
    ) == qualification._sha256_file(launcher)

    if os.name != "nt":
        launcher.chmod(0o750)
        with pytest.raises(qualification.QualificationFailure, match="owner-only"):
            qualification._validate_owner_broker_launcher(
                launcher,
                host_binary=host,
                repository=repository,
            )
        launcher.chmod(0o700)
    launcher.write_bytes(host.read_bytes())
    with pytest.raises(qualification.QualificationFailure, match="process-separated"):
        qualification._validate_owner_broker_launcher(
            launcher,
            host_binary=host,
            repository=repository,
        )

    inside = repository / "owner-broker"
    inside.write_bytes(b"credential-broker")
    inside.chmod(0o700)
    with pytest.raises(qualification.QualificationFailure, match="outside the repository"):
        qualification._validate_owner_broker_launcher(
            inside,
            host_binary=host,
            repository=repository,
        )


def test_owner_broker_launcher_symlink_is_rejected(tmp_path: Path) -> None:
    host = tmp_path / "codex"
    host.write_bytes(b"codex-host")
    host.chmod(0o700)
    target = tmp_path / "owner-broker-target"
    target.write_bytes(b"credential-broker")
    target.chmod(0o700)
    launcher = tmp_path / "owner-broker"
    try:
        launcher.symlink_to(target)
    except OSError:
        pytest.skip("symbolic links are unavailable")
    repository = tmp_path / "repository"
    repository.mkdir()
    with pytest.raises(qualification.QualificationFailure, match="owner-only"):
        qualification._validate_owner_broker_launcher(
            launcher,
            host_binary=host,
            repository=repository,
        )


def test_returned_model_identity_does_not_promote_request_model_pin() -> None:
    assert qualification._returned_model_identity(
        {
            "model": qualification.MODEL,
            "actual_response_provider_id": "openai",
            "actual_response_model_id": "gpt-5.6-luna",
        }
    ) == ("openai", "gpt-5.6-luna")
    assert qualification._returned_model_identity(
        {"model": qualification.MODEL, "effort": qualification.REASONING_EFFORT}
    ) == (None, None)
    with pytest.raises(qualification.QualificationFailure, match="model identity"):
        qualification._returned_model_identity(
            {"actual_response_model_id": "/private/auth/model"}
        )


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
    calls: list[list[str]] = []
    checkpoint_count = 0

    def fake_cli(executable: Path, arguments: list[str], *, cwd: Path, environment=None):
        nonlocal checkpoint_count
        calls.append([str(item) for item in arguments])
        if arguments[1:3] == ["sink", "enable"]:
            return {"grant_id": "grant_pass13"}
        if arguments[1:3] == ["task", "start"]:
            return {
                "status": "ready",
                "task_handle": f"handle_{len(calls):024x}",
            }
        if arguments[1:3] == ["task", "checkpoint"]:
            checkpoint_count += 1
            return {
                "status": "checkpointed",
                "knowledge_id": f"knowledge_{checkpoint_count:024x}",
                "revision_id": f"knowledgerev_{checkpoint_count:024x}",
                "run_id": f"run_{checkpoint_count:024x}",
            }
        if arguments[1:3] == ["autonomy", "status"]:
            return {"audit_head": f"{len(calls) + 1:064x}"}
        return {}

    monkeypatch.setattr(qualification, "_run_installed_cli", fake_cli)
    seeded = qualification._seed_vault(
        Path("/opt/deeplaw"),
        tmp_path / "vault",
        {"cold_start": qualification._make_binding("cold_start")},
        work_dir=tmp_path,
    )
    assert seeded["grant_id"] == "grant_pass13"
    starts = [call for call in calls if call[1:3] == ["task", "start"]]
    checkpoints = [call for call in calls if call[1:3] == ["task", "checkpoint"]]
    assert len(starts) == 3
    assert len(checkpoints) == 4
    assert all("--confirm-no-case-data" in call for call in checkpoints)
    assert not any(call[1:3] == ["sink", "apply"] for call in calls)
    assert all("task_binding" not in " ".join(call) for call in calls)


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
                "host-continuity-qualification.v2.schema.json": hashlib.sha256(
                    (
                        qualification._repository()
                        / "contracts"
                        / "host-continuity-qualification.v2.schema.json"
                    ).read_bytes()
                ).hexdigest(),
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
            "version": qualification.HISTORICAL_CODEX_VERSION_FIXTURE,
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
    resolved_sessions: list[str] = []

    class FakeClient:
        def initialize(self):
            calls.append("initialize")

        def thread_start(self, *args, **kwargs):
            calls.append("thread/start")
            return {
                "thread": {
                    "id": "t1",
                    "sessionId": "session-root",
                    "forkedFromId": None,
                }
            }

        def turn_start(self, *args, **kwargs):
            calls.append("turn/start")
            turn_params.append(kwargs["params"])
            return FakeResult(thread_id="t1", turn_id="u1", final_text="{}")

        def thread_resume(self, *args, **kwargs):
            calls.append("thread/resume")
            return {
                "thread": {
                    "id": "t1",
                    "sessionId": "session-root",
                    "forkedFromId": None,
                }
            }

        def thread_fork(self, *args, **kwargs):
            calls.append("thread/fork")
            return {
                "thread": {
                    "id": "t2",
                    "sessionId": "session-root",
                    "forkedFromId": "t1",
                }
            }

        def thread_compact_start(self, *args, **kwargs):
            calls.append("thread/compact/start")
            return {"status": "started"}

        def close(self):
            calls.append("close")

    monkeypatch.setattr(qualification, "CodexAppServerClient", FakeClient)
    with pytest.raises(qualification.QualificationFailure, match="did not complete"):
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
            bind_host_session=lambda *_args: {"status": "bound"},
            resolve_continuity=lambda session_id: (
                resolved_sessions.append(session_id) or {"status": "admitted"}
            ),
        )
    assert turn_params == [{"outputSchema": qualification._FINAL_RESPONSE_SCHEMA}]
    assert resolved_sessions == ["session-root"]


@pytest.mark.parametrize(
    ("scenario", "development", "expected"),
    [
        ("cold_start", False, True),
        ("compaction_forget", False, True),
        ("resume_fork", False, False),
        ("cold_start", True, False),
    ],
)
def test_only_resume_lifecycles_use_persisted_threads(
    scenario: str,
    development: bool,
    expected: bool,
) -> None:
    assert qualification._thread_is_ephemeral(scenario, development=development) is expected


def test_persisted_fork_keeps_hook_session_root_and_records_thread_lineage() -> None:
    assert qualification._persisted_fork_identity(
        {
            "thread": {
                "id": "thread-child",
                "sessionId": "session-root",
                "forkedFromId": "thread-parent",
            }
        },
        parent_thread_id="thread-parent",
        root_session_id="session-root",
    ) == ("thread-child", "session-root", "thread-parent")

    with pytest.raises(
        qualification.QualificationFailure,
        match="preserve the root session",
    ):
        qualification._persisted_fork_identity(
            {
                "thread": {
                    "id": "thread-child",
                    "sessionId": "session-child",
                    "forkedFromId": "thread-parent",
                }
            },
            parent_thread_id="thread-parent",
            root_session_id="session-root",
        )


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
    }
    with pytest.raises(qualification.QualificationFailure, match="did not complete"):
        qualification._turn_record(result, **kwargs)

    result["status"] = "completed"
    observation = result["tool_call_observations"][0]
    for required_field in ("argument_task_present", "argument_query_plan_version"):
        retained = observation.pop(required_field)
        with pytest.raises(qualification.QualificationFailure, match="safe read"):
            qualification._turn_record(result, **kwargs)
        observation[required_field] = retained

    unbound_record, _ = qualification._turn_record(result, **kwargs)
    assert unbound_record["status"] == "passed"
    observation["argument_task_binding_sha256"] = "a" * 64
    with pytest.raises(qualification.QualificationFailure, match="exposed"):
        qualification._turn_record(result, **kwargs)
    observation.pop("argument_task_binding_sha256")

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


def test_turn_record_requires_exact_native_hook_delivery_and_zero_tools() -> None:
    capsule = {
        "schema_version": "deeplaw.host-continuity-capsule/v1",
        "status": "admitted",
        "statements": [],
        "gaps": [],
        "conflicts": [],
        "write_performed": False,
    }
    capsule_text = pass13_evidence.canonical_json(capsule)
    context = qualification._CONTINUITY_CONTEXT_PREFIX + capsule_text
    encoded = context.encode("utf-8")
    expected = {
        "status": "admitted",
        "capsule_sha256": hashlib.sha256(capsule_text.encode("utf-8")).hexdigest(),
        "capsule_bytes": len(capsule_text.encode("utf-8")),
        "context_sha256": hashlib.sha256(encoded).hexdigest(),
        "context_bytes": len(encoded),
        "statement_count": 0,
        "gap_codes": [],
        "conflict_count": 0,
        "_capsule": capsule,
        "_context_text": context,
        "_provider_payload": {
            "operation": "resolve-host-continuity",
            "provider_bytes": len(encoded),
            "provider_sha256": hashlib.sha256(encoded).hexdigest(),
            "structured_output_bytes": None,
            "structured_output_sha256": None,
            "delivery_match": True,
            "write_performed": False,
            "statement_count": 0,
            "gap_count": 0,
            "gap_codes": [],
            "relevant_chars": 0,
            "context_chars": len(context),
            "relevant_chars_context_chars": 0.0,
            "evidence_count": 0,
            "duplicate_evidence_count": 0,
            "duplicate_evidence_rate": None,
            "conflict_count": 0,
        },
    }
    delivery = {
        "method": "hook/completed",
        "hook_event_name": "userPromptSubmit",
        "hook_status": "completed",
        "hook_source": "plugin",
        "hook_handler_type": "command",
        "continuity_context_sha256": expected["context_sha256"],
        "continuity_context_bytes": expected["context_bytes"],
        "continuity_status": "admitted",
        "continuity_statement_count": 0,
        "continuity_gap_codes": [],
        "continuity_conflict_count": 0,
    }
    result = {
        "status": "completed",
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
        "tool_call_observations": [],
        "tool_outputs": [],
        "usage": {
            "input_tokens": 10,
            "cached_input_tokens": 2,
            "cache_write_input_tokens": 0,
            "output_tokens": 5,
            "reasoning_output_tokens": 1,
            "total_tokens": 15,
        },
        "events": [delivery],
    }
    kwargs = {
        "lifecycle_method": "thread/start",
        "prompt": "fixture",
        "ledger_before": "a" * 64,
        "ledger_after": "a" * 64,
        "expected_continuity": expected,
    }
    record, _payload = qualification._turn_record(result, **kwargs)
    assert record["safe_read"]["call_count"] == 0
    assert record["safe_read"]["safe_read_operations"] == [
        "resolve-host-continuity"
    ]
    assert record["safe_read"]["provider_payloads"][0]["delivery_match"] is True

    result["tool_call_observations"] = [{"tool_name": "knowledge_support"}]
    with pytest.raises(qualification.QualificationFailure, match="Provider-side tool"):
        qualification._turn_record(result, **kwargs)
    result["tool_call_observations"] = []

    result["events"] = []
    with pytest.raises(qualification.QualificationFailure, match="not observed exactly once"):
        qualification._turn_record(result, **kwargs)
    result["events"] = [{**delivery, "continuity_context_sha256": "f" * 64}]
    with pytest.raises(qualification.QualificationFailure, match="did not match"):
        qualification._turn_record(result, **kwargs)


def test_precompact_delivery_binds_the_checkpoint_gap_and_exact_context() -> None:
    capsule = {
        "schema_version": "deeplaw.host-continuity-capsule/v1",
        "status": "admitted",
        "statements": [
            {
                "content": "Continue the bounded plan.",
                "authority": "agent_derived",
                "legal_authority": False,
                "valid_from": None,
                "valid_to": None,
                "citations": [],
            }
        ],
        "gaps": [],
        "conflicts": [],
        "write_performed": False,
    }
    raw = pass13_evidence.canonical_json(capsule).encode("utf-8")
    context = qualification._CONTINUITY_CONTEXT_PREFIX + raw.decode("utf-8")
    continuity = {
        "status": "admitted",
        "capsule_sha256": hashlib.sha256(raw).hexdigest(),
        "capsule_bytes": len(raw),
        "context_sha256": hashlib.sha256(context.encode()).hexdigest(),
        "context_bytes": len(context.encode()),
        "statement_count": 1,
        "gap_codes": [],
        "conflict_count": 0,
        "_capsule": capsule,
        "_context_text": context,
        "_provider_payload": {
            "provider_bytes": len(context.encode()),
            "provider_sha256": hashlib.sha256(context.encode()).hexdigest(),
            "gap_count": 0,
            "gap_codes": [],
            "context_chars": len(context),
            "statement_count": 1,
            "conflict_count": 0,
        },
    }
    expected = qualification._continuity_with_checkpoint_gap(continuity)
    assert expected["gap_codes"] == ["checkpoint_grant_missing"]
    delivery = {
        "method": "hook/completed",
        "hook_event_name": "preCompact",
        "hook_status": "completed",
        "hook_source": "plugin",
        "hook_handler_type": "command",
        "continuity_context_sha256": expected["context_sha256"],
        "continuity_context_bytes": expected["context_bytes"],
        "continuity_status": expected["status"],
        "continuity_statement_count": expected["statement_count"],
        "continuity_gap_codes": expected["gap_codes"],
        "continuity_conflict_count": expected["conflict_count"],
    }
    assert qualification._require_codex_continuity_delivery(
        [delivery], expected_continuity=expected, event_name="preCompact"
    ) == delivery
    with pytest.raises(qualification.QualificationFailure, match="did not match"):
        qualification._require_codex_continuity_delivery(
            [{**delivery, "continuity_context_sha256": "f" * 64}],
            expected_continuity=expected,
            event_name="preCompact",
        )


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
