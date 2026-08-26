from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path

import pytest

from benchmarks.hosts import pass13_evidence
from benchmarks.hosts import (
    run_pass13_opencode_continuity_qualification as runner,
)
from deeplaw.task_context import build_task_context_binding

_TASK_BINDING = build_task_context_binding(
    "1" * 64,
    "2" * 64,
    repository_sha256="3" * 64,
    worktree_sha256="4" * 64,
    base_revision="5" * 40,
    dirty_state_sha256="6" * 64,
)

_REPOSITORY = Path(__file__).resolve().parents[1]
_HOST_TASK_CASES = _REPOSITORY / "benchmarks/hosts/v013-host-task-cases-v1.json"
_GATE_CLASSIFICATION = (
    _REPOSITORY / "benchmarks/release/v013-gate-classification-v9.json"
)


@pytest.fixture(autouse=True)
def _candidate_wheel_plugin_fixture(monkeypatch: pytest.MonkeyPatch) -> None:
    plugin = (
        Path(__file__).resolve().parents[1]
        / "adapters"
        / "opencode"
        / "plugins"
        / "deeplaw-native.ts"
    )
    monkeypatch.setattr(
        runner,
        "_installed_opencode_plugin_bytes",
        lambda _deeplaw_executable: plugin.read_bytes(),
    )


def _capsule(marker: str = "NEXT_ACTION") -> dict[str, object]:
    return {
        "schema_version": "deeplaw.knowledge-capsule-projection/v1",
        "projection": "standard",
        "receipt_id": "queryreceipt_" + "b" * 24,
        "hard_limit_bytes": 65_536,
        "statements": [
            {
                "statement_id": "statement_" + "a" * 24,
                "statement_text": marker,
                "statement_type": "factual",
                "support_status": "supported",
                "current_supported": True,
                "freshness": "fresh",
                "origin": "agent_derived",
                "authority": "agent_memory",
                "verification": "unverified",
                "legal_authority": False,
                "source_refs": [],
            }
        ],
        "evidence": [],
        "gaps": [],
        "selected_statement_count": 1,
        "selected_source_count": 0,
    }


def _tool_output(*, operation: str = "context", marker: str = "NEXT_ACTION") -> dict[str, object]:
    capsule = _capsule(marker)
    text = pass13_evidence.canonical_json(capsule)
    return {
        "content": [{"type": "text", "text": text}],
        "structuredContent": {
            "schema_version": "deeplaw.knowledge-support-output/v6",
            "operation": operation,
            "authority_boundary": {
                "legal_authority": False,
                "official_legal_sources_tool": "law_support",
                "persistent_writes": "separate_explicit_knowledge_sink",
                "case_data_allowed": False,
                "authority_from_ranking": False,
            },
            "result": {
                "schema_version": "deeplaw.provider-knowledge-capsule/v2",
                "purpose": "answer",
                "policy_id": "compiled-first-v1",
                "capsule": capsule,
                "receipt": {"receipt_id": "queryreceipt_" + "b" * 24},
                "delivery": {
                    "hard_limit_bytes": 65_536,
                    "provider_content_bytes": len(text.encode("utf-8")),
                    "projection": "standard",
                    "write_performed": False,
                },
            },
        },
    }


def _insufficient_output() -> dict[str, object]:
    output = _tool_output(marker="FIRST")
    capsule = output["structuredContent"]["result"]["capsule"]  # type: ignore[index]
    capsule["statements"] = []  # type: ignore[index]
    capsule["selected_statement_count"] = 0  # type: ignore[index]
    capsule["gaps"] = [  # type: ignore[index]
        {
            "gap_id": "querygap_" + "1" * 24,
            "code": "insufficient_context",
            "duty": "unresolved_gap",
            "message": "First bounded read was insufficient.",
        }
    ]
    text = pass13_evidence.canonical_json(capsule)
    output["content"][0]["text"] = text  # type: ignore[index]
    output["structuredContent"]["result"]["delivery"][  # type: ignore[index]
        "provider_content_bytes"
    ] = len(text.encode("utf-8"))
    return output


def _event(call_index: int = 1, *, output: dict[str, object] | None = None) -> dict[str, object]:
    selected = output or _tool_output()
    return {
        "type": "tool_use",
        "part": {
            "tool": runner.TOOL_NAME,
            "callID": f"call-{call_index}",
            "state": {
                "status": "completed",
                "input": {
                    "operation": selected["structuredContent"]["operation"],  # type: ignore[index]
                    "task": "Pass 13 fixture",
                    "confirm_no_case_data": True,
                    "query_plan_version": "6",
                    "task_binding": _TASK_BINDING,
                },
                # OpenCode 1.18.16 stores the joined MCP text content in the
                # native tool event, not the complete CallToolResult object.
                "output": selected["content"][0]["text"],  # type: ignore[index]
                "metadata": {"truncated": False},
            },
        },
        "sessionID": "session-fixture",
        "messageID": f"message-{call_index}",
    }


def _events(*outputs: dict[str, object]) -> bytes:
    rows: list[dict[str, object]] = []
    for index, output in enumerate(outputs, start=1):
        rows.append(_event(index, output=output))
    rows.extend(
        [
            {
                "type": "step_finish",
                "part": {
                    "tokens": {
                        "input": 10,
                        "cache_read": 2,
                        "cache_write": 0,
                        "output": 4,
                        "reasoning": 1,
                        "total": 17,
                    }
                },
                "sessionID": "session-fixture",
                "messageID": "message-finish",
            },
            {
                "type": "text",
                "part": {
                    "text": pass13_evidence.canonical_json(
                        {
                            "summary": "bounded",
                            "next_step": "NEXT_ACTION",
                            "preserved_decisions": ["CURRENT_DECISION"],
                            "open_gaps": [],
                        }
                    )
                },
                "sessionID": "session-fixture",
                "messageID": "message-final",
            },
        ]
    )
    return b"".join(
        (json.dumps(row, ensure_ascii=False, separators=(",", ":")) + "\n").encode("utf-8")
        for row in rows
    )


def _availability_events() -> bytes:
    rows = [
        {
            "type": "step_finish",
            "part": {
                "tokens": {
                    "input": 2,
                    "cache": {"read": 0, "write": 0},
                    "output": 1,
                    "reasoning": 0,
                    "total": 3,
                }
            },
        },
        {"type": "text", "part": {"text": "available"}},
    ]
    return b"".join(
        (json.dumps(row, separators=(",", ":")) + "\n").encode("utf-8") for row in rows
    )


def _preflight_plugin_fixture(tmp_path: Path) -> tuple[Path, dict[str, object], dict[str, object]]:
    project = tmp_path / "preflight-project"
    project.mkdir()
    run_root = tmp_path / "preflight-run"
    run_root.mkdir()
    receipt, _ = runner._install_exact_opencode_plugin(
        repository=project,
        run_root=run_root,
        deeplaw_executable=tmp_path / "deeplaw",
    )
    resolved = runner.build_opencode_config()
    resolved["plugin"] = [(project / runner._PLUGIN_INSTALLED_RELATIVE).as_uri()]
    return project, receipt, resolved


def test_runner_has_no_dotenv_or_provider_secret_input_surface() -> None:
    source = Path(runner.__file__).read_text(encoding="utf-8")
    assert not hasattr(runner, "load_deepseek_key")
    assert "--dotenv" not in source
    assert "load_deepseek_key" not in source


def test_sensitive_scan_accepts_public_https_but_rejects_private_paths() -> None:
    runner._forbid_sensitive(b'{"schema":"https://opencode.ai/config.json"}')
    with pytest.raises(runner.QualificationError, match="absolute path"):
        runner._forbid_sensitive(b'{"path":"C:\\\\private\\\\runtime"}')


def test_permission_and_config_are_exactly_read_only() -> None:
    permission = runner.build_permission()
    assert permission == {
        "*": "deny",
        runner.TOOL_NAME: "allow",
    }
    config = runner.build_opencode_config()
    assert config["model"] == runner.MODEL
    assert config["small_model"] == runner.MODEL
    assert config["permission"] == permission
    assert config["agent"]["qualification"]["permission"] == permission  # type: ignore[index]
    prompt = config["agent"]["qualification"]["prompt"]  # type: ignore[index]
    assert "continuity capsule" in prompt.casefold()
    assert "do not invoke any tool" in prompt.casefold()
    assert "knowledge_support" not in prompt
    assert "every response string non-empty and at most 200 characters" in prompt
    assert "each response array to one through three items" in prompt
    assert set(config["mcp"]) == {"deeplaw_knowledge"}  # type: ignore[arg-type]


def test_project_plugin_mode_has_no_legacy_pure_or_project_config_bypass(
    tmp_path: Path,
) -> None:
    environment = runner.build_host_environment(
        root=tmp_path,
        opencode_binary=tmp_path / "bin" / "opencode",
        node_binary=tmp_path / "bin" / "node",
    )
    assert "OPENCODE_PURE" not in environment
    assert "OPENCODE_DISABLE_PROJECT_CONFIG" not in environment
    assert "--pure" not in runner._opencode_cli_turn_args()
    assert "plugin" not in runner.build_opencode_config()
    catalog = json.loads(_HOST_TASK_CASES.read_text(encoding="utf-8"))
    classification = json.loads(_GATE_CLASSIFICATION.read_text(encoding="utf-8"))
    runner_prefix = ["opencode", *runner._opencode_cli_turn_args()[:3]]
    opencode_gate = next(
        row for row in classification["gates"] if row["gate_id"] == "opencode"
    )

    assert runner_prefix == ["opencode", "run", "--format", "json"]
    assert catalog["host_constraints"]["opencode"]["argv_prefix"] == runner_prefix
    assert opencode_gate["constraints"]["argv_prefix"] == runner_prefix
    assert opencode_gate["constraints"]["plugin_policy"] == (
        "single_exact_candidate_plugin"
    )
    assert opencode_gate["constraints"]["ambient_project_plugins"] == "forbidden"

def test_prepare_scenario_state_installs_exact_plugin_bytes_and_receipt(
    tmp_path: Path,
) -> None:
    repository = tmp_path / "repository"
    repository.mkdir()
    run_root = tmp_path / "run"
    run_root.mkdir()
    _environment, _wrapper_receipt, plugin_receipt = runner._prepare_scenario_state(
        base_environment={},
        run_root=run_root,
        repository=repository,
        deeplaw_executable=tmp_path / "deeplaw",
        node_binary=tmp_path / "node",
    )
    source = (
        Path(__file__).resolve().parents[1]
        / "adapters"
        / "opencode"
        / "plugins"
        / "deeplaw-native.ts"
    )
    target = repository / runner._PLUGIN_INSTALLED_RELATIVE
    assert target.read_bytes() == source.read_bytes()
    assert plugin_receipt["exact_match"] is True
    assert plugin_receipt["source_sha256"] == plugin_receipt["installed_sha256"]
    assert plugin_receipt["source_bytes"] == plugin_receipt["installed_bytes"]
    assert (run_root / "opencode-plugin-receipt.json").is_file()
    assert _environment["PATH"].split(os.pathsep)[0] == str(tmp_path)
    assert Path(_environment["DEEPLAW_KNOWLEDGE_VAULT"]).is_absolute()
    assert Path(_environment["DEEPLAW_KNOWLEDGE_VAULT"]) == (repository / "vault").resolve()

    broken_repository = tmp_path / "broken-repository"
    broken_repository.mkdir()
    broken_target = broken_repository / runner._PLUGIN_INSTALLED_RELATIVE
    broken_target.parent.mkdir(parents=True)
    os.symlink(tmp_path / "missing-plugin.ts", broken_target)
    with pytest.raises(runner.QualificationError, match="regular file"):
        runner._install_exact_opencode_plugin(
            repository=broken_repository,
            run_root=tmp_path / "broken-run",
            deeplaw_executable=tmp_path / "deeplaw",
        )


def test_host_environment_is_allowlisted_and_isolated(tmp_path: Path) -> None:
    environment = runner.build_host_environment(
        root=tmp_path,
        opencode_binary=tmp_path / "bin" / "opencode",
        node_binary=tmp_path / "bin" / "node",
        canaries={name: f"canary-{index}" for index, name in enumerate(runner._CANARY_NAMES)},
    )
    assert "DEEPSEEK_API_KEY" not in environment
    assert set(runner._CANARY_NAMES) <= set(environment)
    assert "HOME" in environment and environment["HOME"].startswith(str(tmp_path))
    assert "USERPROFILE" in environment
    assert "APPDATA" in environment
    assert "XDG_CONFIG_HOME" in environment
    assert set(environment) <= runner.EXPECTED_HOST_ENVIRONMENT_NAMES


def test_process_group_options_fail_closed_for_platform() -> None:
    options = runner.process_creation_options()
    if os.name == "nt":
        assert options["creationflags"]
    else:
        assert options["start_new_session"] is True


def test_owner_broker_launcher_must_be_owner_only_and_process_separated(
    tmp_path: Path,
) -> None:
    host = tmp_path / "opencode"
    host.write_bytes(b"host-binary")
    launcher = tmp_path / "owner-broker"
    launcher.write_bytes(b"broker-launcher")
    launcher.chmod(0o700)
    assert runner._validate_owner_broker_launcher(
        launcher, host_binary=host
    ) == hashlib.sha256(b"broker-launcher").hexdigest()

    if os.name != "nt":
        launcher.chmod(0o750)
        with pytest.raises(runner.QualificationError, match="owner-only"):
            runner._validate_owner_broker_launcher(launcher, host_binary=host)
        launcher.chmod(0o700)
    launcher.write_bytes(host.read_bytes())
    with pytest.raises(runner.QualificationError, match="process-separated"):
        runner._validate_owner_broker_launcher(launcher, host_binary=host)


def test_timeout_terminates_the_created_process_group(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    class FakeProcess:
        pid = 123
        returncode = -9

        def communicate(
            self, *, input: bytes = b"", timeout: float | None = None
        ) -> tuple[bytes, bytes]:
            del input
            if timeout is not None:
                raise runner.subprocess.TimeoutExpired("fake", timeout)
            return b"", b""

        def poll(self) -> None:
            return None

    fake = FakeProcess()
    killed: list[object] = []
    monkeypatch.setattr(runner.subprocess, "Popen", lambda *args, **kwargs: fake)
    monkeypatch.setattr(runner, "_terminate_process_tree", lambda process: killed.append(process))
    result = runner._run_bounded_process(
        ["fake-opencode"], environment={"PATH": os.defpath}, cwd=tmp_path, timeout=0.01
    )
    assert result["timed_out"] is True
    assert killed == [fake]


def test_mcp_receipt_proves_provider_and_auth_are_absent() -> None:
    receipt = {
        "environment_names": sorted(runner.EXPECTED_MCP_ENVIRONMENT_NAMES),
        "blocked_host_names_present": sorted(
            {"DEEPSEEK_API_KEY", *runner._CANARY_NAMES}
        ),
        "blocked_child_names_present": [],
        "child_argv": [
            "deeplaw",
            "knowledge",
            "mcp",
            "--closed-environment",
            "--stdio",
        ],
        "wrapper_sha256": "a" * 64,
        "child_executable_sha256": "b" * 64,
        "environment_sha256": "c" * 64,
    }
    assert runner.validate_mcp_receipt(receipt) is True
    receipt["environment_names"].append("DEEPSEEK_API_KEY")  # type: ignore[union-attr]
    with pytest.raises(runner.QualificationError, match="closed"):
        runner.validate_mcp_receipt(receipt)


def test_model_inventory_requires_selected_model_and_keeps_only_hashes() -> None:
    inventory = runner.parse_model_inventory(
        b"deepseek/deepseek-v4-flash\ndeepseek/deepseek-chat\n", returncode=0
    )
    assert inventory["selected_present"] is True
    assert inventory["raw_bytes"] == len(b"deepseek/deepseek-v4-flash\ndeepseek/deepseek-chat\n")
    assert (
        inventory["raw_sha256"]
        == hashlib.sha256(b"deepseek/deepseek-v4-flash\ndeepseek/deepseek-chat\n").hexdigest()
    )
    with pytest.raises(runner.QualificationError, match="selected model"):
        runner.parse_model_inventory(b"deepseek/deepseek-chat\n", returncode=0)


def test_session_identity_is_safe_for_cli_and_loopback_paths() -> None:
    assert "session-fixture" in runner._opencode_cli_turn_args(
        session_id="session-fixture"
    )
    assert (
        runner._session_id_from_events(b'{"sessionID":"session-fixture"}\n')
        == "session-fixture"
    )
    for invalid in ("../session", "session/value", "session value", "session%2fvalue"):
        with pytest.raises(runner.QualificationError, match="session identity"):
            runner._opencode_cli_turn_args(session_id=invalid)
        with pytest.raises(runner.QualificationError, match="session identity"):
            runner._session_id_from_events(
                (json.dumps({"sessionID": invalid}) + "\n").encode("utf-8")
            )


def test_preflight_keeps_owner_broker_out_of_static_inspection_commands(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    project, plugin_receipt, resolved = _preflight_plugin_fixture(tmp_path)
    calls: list[tuple[tuple[str, ...], dict[str, str]]] = []
    availability_environments: list[dict[str, str]] = []

    def run_command(
        binary: Path,
        *,
        args: tuple[str, ...],
        environment: dict[str, str],
        cwd: Path,
    ) -> dict[str, object]:
        del binary, cwd
        calls.append((args, environment))
        if args == ("--version",):
            stdout = b"1.18.16\n"
        elif args == ("models", "deepseek"):
            stdout = b"deepseek/deepseek-v4-flash\n"
        else:
            assert args == ("debug", "config")
            stdout = runner._encoded(resolved)
        return {
            "stdout": stdout,
            "stderr": b"",
            "returncode": 0,
            "elapsed_ms": 1,
            "timed_out": False,
            "output_overflow": False,
        }

    monkeypatch.setattr(runner, "_run_opencode_command", run_command)

    def availability(*args, **kwargs):
        del args
        availability_environments.append(dict(kwargs["environment"]))
        return {"status": "available"}

    monkeypatch.setattr(runner, "_probe_model_availability", availability)
    receipt = runner.preflight_opencode(
        binary=tmp_path / "opencode",
        host_launcher=tmp_path / "owner-broker",
        deeplaw_executable=tmp_path / "deeplaw",
        environment={
            **{name: f"canary-{name}" for name in runner._CANARY_NAMES},
        },
        cwd=tmp_path,
        project_root=project,
        plugin_receipt=plugin_receipt,
    )
    assert calls
    assert all("DEEPSEEK_API_KEY" not in environment for _args, environment in calls)
    assert all(
        set(runner._CANARY_NAMES).isdisjoint(environment)
        for _args, environment in calls
    )
    assert len(availability_environments) == 1
    availability_environment = availability_environments[0]
    assert "DEEPSEEK_API_KEY" not in availability_environment
    assert all(
        availability_environment[name] == f"canary-{name}"
        for name in runner._CANARY_NAMES
    )
    assert availability_environment["OPENCODE_CONFIG"].endswith(
        "availability-opencode.json"
    )
    assert receipt["model_inventory"]["selected_present"] is True  # type: ignore[index]

    def leaking_command(
        binary: Path,
        *,
        args: tuple[str, ...],
        environment: dict[str, str],
        cwd: Path,
    ) -> dict[str, object]:
        result = run_command(binary, args=args, environment=environment, cwd=cwd)
        if args == ("models", "deepseek"):
            result["stdout"] = b"deepseek/deepseek-v4-flash\ncanary-leak"
        return result

    monkeypatch.setattr(runner, "_run_opencode_command", leaking_command)
    with pytest.raises(runner.QualificationError, match="forbidden value"):
        runner.preflight_opencode(
            binary=tmp_path / "opencode",
            host_launcher=tmp_path / "owner-broker",
            deeplaw_executable=tmp_path / "deeplaw",
            environment={runner._CANARY_NAMES[0]: "canary-leak"},
            cwd=tmp_path,
            project_root=project,
            plugin_receipt=plugin_receipt,
        )

    resolved["instructions"] = ["D:/private/runtime"]
    monkeypatch.setattr(runner, "_run_opencode_command", run_command)
    with pytest.raises(runner.QualificationError, match="absolute path"):
        runner.preflight_opencode(
            binary=tmp_path / "opencode",
            host_launcher=tmp_path / "owner-broker",
            deeplaw_executable=tmp_path / "deeplaw",
            environment={},
            cwd=tmp_path,
            project_root=project,
            plugin_receipt=plugin_receipt,
        )


def test_preflight_rejects_a_canary_resolved_into_debug_config(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    canary = "qualification-canary"
    project, plugin_receipt, resolved = _preflight_plugin_fixture(tmp_path)
    resolved["provider"]["deepseek"]["options"]["apiKey"] = canary  # type: ignore[index]

    def run_command(
        binary: Path,
        *,
        args: tuple[str, ...],
        environment: dict[str, str],
        cwd: Path,
    ) -> dict[str, object]:
        del binary, environment, cwd
        outputs = {
            ("--version",): b"1.18.16\n",
            ("models", "deepseek"): b"deepseek/deepseek-v4-flash\n",
            ("debug", "config"): runner._encoded(resolved),
        }
        return {
            "stdout": outputs[args],
            "stderr": b"",
            "returncode": 0,
            "elapsed_ms": 1,
            "timed_out": False,
            "output_overflow": False,
        }

    monkeypatch.setattr(runner, "_run_opencode_command", run_command)
    with pytest.raises(runner.QualificationError, match="forbidden value"):
        runner.preflight_opencode(
            binary=tmp_path / "opencode",
            host_launcher=tmp_path / "owner-broker",
            deeplaw_executable=tmp_path / "deeplaw",
            environment={runner._CANARY_NAMES[0]: canary},
            cwd=tmp_path,
            project_root=project,
            plugin_receipt=plugin_receipt,
        )


def test_resolved_config_must_bind_the_unique_installed_project_plugin(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    project = tmp_path / "project"
    project.mkdir()
    run_root = tmp_path / "run"
    run_root.mkdir()
    receipt, _ = runner._install_exact_opencode_plugin(
        repository=project,
        run_root=run_root,
        deeplaw_executable=tmp_path / "deeplaw",
    )
    resolved = runner.build_opencode_config()
    target = project / runner._PLUGIN_INSTALLED_RELATIVE
    resolved["plugin"] = [target.as_uri()]

    def run_command(
        binary: Path,
        *,
        args: tuple[str, ...],
        environment: dict[str, str],
        cwd: Path,
    ) -> dict[str, object]:
        del binary, environment, cwd
        outputs = {
            ("--version",): b"1.18.16\n",
            ("models", "deepseek"): b"deepseek/deepseek-v4-flash\n",
            ("debug", "config"): runner._encoded(resolved),
        }
        return {
            "stdout": outputs[args],
            "stderr": b"",
            "returncode": 0,
            "elapsed_ms": 1,
            "timed_out": False,
            "output_overflow": False,
        }

    monkeypatch.setattr(runner, "_run_opencode_command", run_command)
    monkeypatch.setattr(
        runner,
        "_probe_model_availability",
        lambda *args, **kwargs: {"status": "available"},
    )
    result = runner.preflight_opencode(
        binary=tmp_path / "opencode",
        host_launcher=tmp_path / "owner-broker",
        deeplaw_executable=tmp_path / "deeplaw",
        environment={},
        cwd=tmp_path,
        project_root=project,
        plugin_receipt=receipt,
    )
    assert result["external_plugin"]["exact_match"] is True  # type: ignore[index]

    resolved["plugin"] = []
    with pytest.raises(runner.QualificationError, match="exact project plugin"):
        runner.preflight_opencode(
            binary=tmp_path / "opencode",
            host_launcher=tmp_path / "owner-broker",
            deeplaw_executable=tmp_path / "deeplaw",
            environment={},
            cwd=tmp_path,
            project_root=project,
            plugin_receipt=receipt,
        )


def test_no_model_availability_probe_is_separate_and_sanitized() -> None:
    receipt = runner.parse_availability_result(
        stdout=_availability_events(),
        returncode=0,
        elapsed_ms=27,
    )
    assert receipt["status"] == "available"
    assert receipt["raw_bytes"] == len(_availability_events())
    assert receipt["elapsed_ms"] == 27
    assert 'status":"ok' not in json.dumps(receipt)
    with pytest.raises(runner.QualificationError, match=r"forbidden field|unexpected event"):
        runner.parse_availability_result(
            stdout=_events(_tool_output()), returncode=0, elapsed_ms=1
        )


def test_opencode_failure_codes_are_safe_constant_labels() -> None:
    assert runner._safe_failure_code(
        runner.QualificationError("final response schema is invalid")
    ) == "final_response_schema_invalid"
    assert runner._safe_failure_code(
        runner.QualificationError("provider included unsafe arbitrary text")
    ) == "host_qualification_failure"
    assert runner._safe_failure_code(
        runner.QualificationError("evidence contains a forbidden value")
    ) == "secret_or_canary_leak"
    assert runner._safe_failure_code(
        runner.QualificationError("current Provider Capsule is missing")
    ) == "provider_capsule_invalid"
    assert runner._safe_failure_code(
        runner.QualificationError("OpenCode resume tool call did not complete")
    ) == "cli_resume_tool_failed"
    assert runner._safe_failure_code(
        runner.QualificationError("OpenCode fork final response used a code fence")
    ) == "cli_fork_final_response_fenced"
    assert runner._safe_failure_code(
        runner.QualificationError("MCP call lacks the exact public v6 context arguments")
    ) == "safe_read_call_shape_invalid"


def test_analyzer_accepts_one_or_two_safe_reads_and_rejects_three() -> None:
    one = runner.analyze_opencode_events(
        _events(_tool_output()), expected_task_binding=_TASK_BINDING
    )
    assert one["safe_read"]["call_count"] == 1  # type: ignore[index]
    assert one["safe_read"]["provider_payloads"][0]["structured_output_bytes"] is None  # type: ignore[index]
    assert one["usage"]["total_tokens"] == 17  # type: ignore[index]

    two = runner.analyze_opencode_events(
        _events(_insufficient_output(), _tool_output(marker="SECOND")),
        expected_task_binding=_TASK_BINDING,
    )
    assert two["safe_read"]["call_count"] == 2  # type: ignore[index]
    assert two["safe_read"]["bounded_retry_used"] is True  # type: ignore[index]

    with pytest.raises(runner.QualificationError, match="one or two"):
        runner.analyze_opencode_events(
            _events(_tool_output(), _tool_output(), _tool_output()),
            expected_task_binding=_TASK_BINDING,
        )


def test_analyzer_accepts_native_hook_capsule_without_mcp_tool_call() -> None:
    capsule = {
        "schema_version": "deeplaw.host-continuity-capsule/v1",
        "status": "gap",
        "statements": [],
        "gaps": [{"code": "route_forgotten"}],
        "conflicts": [],
        "write_performed": False,
    }
    rows = [
        {
            "type": "step_finish",
            "part": {
                "tokens": {
                    "input": 10,
                    "cache_read": 0,
                    "cache_write": 0,
                    "output": 4,
                    "reasoning": 1,
                    "total": 15,
                }
            },
            "sessionID": "session-hook",
            "messageID": "message-finish",
        },
        {
            "type": "text",
            "part": {
                "text": pass13_evidence.canonical_json(
                    {
                        "summary": "gap",
                        "next_step": "request owner checkpoint",
                        "preserved_decisions": ["keep bounded"],
                        "open_gaps": ["route_forgotten"],
                    }
                )
            },
            "sessionID": "session-hook",
            "messageID": "message-final",
        },
    ]
    text = pass13_evidence.canonical_json(capsule)
    analysis = runner.analyze_opencode_events(
        b"".join(
            (json.dumps(row, separators=(",", ":")) + "\n").encode("utf-8")
            for row in rows
        ),
        continuity_capsule=capsule,
        continuity_text=text,
    )
    assert analysis["safe_read"]["safe_read_operations"] == ["resolve-host-continuity"]
    assert analysis["safe_read"]["call_count"] == 0
    assert b"tool_use" not in analysis["sanitized_events"]


def test_plugin_delivery_receipt_must_match_exact_native_context() -> None:
    capsule = {
        "schema_version": "deeplaw.host-continuity-capsule/v1",
        "status": "gap",
        "statements": [],
        "gaps": [{"code": "route_forgotten"}],
        "conflicts": [],
        "write_performed": False,
    }
    text = pass13_evidence.canonical_json(capsule)
    session = "session-hook"
    observation = {
        "schema_version": "deeplaw.opencode-continuity-delivery-observation/v1",
        "event_type": "experimental.chat.system.transform",
        "session_sha256": hashlib.sha256(session.encode()).hexdigest(),
        "context_sha256": hashlib.sha256(text.encode()).hexdigest(),
        "context_bytes": len(text.encode()),
        "status": "gap",
        "statement_count": 0,
        "gap_codes": ["route_forgotten"],
        "conflict_count": 0,
    }
    receipt = runner._continuity_delivery_from_observations(
        [observation],
        session_id=session,
        continuity_capsule=capsule,
        continuity_text=text,
    )
    assert receipt["context_sha256"] == observation["context_sha256"]

    with pytest.raises(runner.QualificationError, match="exactly once"):
        runner._continuity_delivery_from_observations(
            [],
            session_id=session,
            continuity_capsule=capsule,
            continuity_text=text,
        )
    with pytest.raises(runner.QualificationError, match="did not match"):
        runner._continuity_delivery_from_observations(
            [{**observation, "context_sha256": "f" * 64}],
            session_id=session,
            continuity_capsule=capsule,
            continuity_text=text,
        )


def test_compaction_delivery_adds_one_checkpoint_gap_to_canonical_bytes() -> None:
    capsule = {
        "schema_version": "deeplaw.host-continuity-capsule/v1",
        "status": "gap",
        "statements": [],
        "gaps": [{"code": "route_forgotten"}],
        "conflicts": [],
        "write_performed": False,
    }
    selected, text = runner._continuity_with_checkpoint_gap(capsule)
    assert [item["code"] for item in selected["gaps"]] == [
        "route_forgotten",
        "checkpoint_grant_missing",
    ]
    assert text == pass13_evidence.canonical_json(selected)
    observation = {
        "schema_version": "deeplaw.opencode-continuity-delivery-observation/v1",
        "event_type": "experimental.session.compacting",
        "session_sha256": hashlib.sha256(b"session-hook").hexdigest(),
        "context_sha256": hashlib.sha256(text.encode()).hexdigest(),
        "context_bytes": len(text.encode()),
        "status": "gap",
        "statement_count": 0,
        "gap_codes": ["checkpoint_grant_missing", "route_forgotten"],
        "conflict_count": 0,
    }
    receipt = runner._continuity_delivery_from_observations(
        [observation],
        session_id="session-hook",
        continuity_capsule=selected,
        continuity_text=text,
        event_type="experimental.session.compacting",
    )
    assert receipt["event_type"] == "experimental.session.compacting"


def test_analyzer_rejects_provider_canonical_mismatch_and_unsafe_operation() -> None:
    mismatched = _tool_output()
    mismatched["content"][0]["text"] = json.dumps(_capsule())  # type: ignore[index]
    with pytest.raises(runner.QualificationError, match="canonical"):
        runner.analyze_opencode_events(
            _events(mismatched), expected_task_binding=_TASK_BINDING
        )

    unsafe = _tool_output(operation="semantic")
    with pytest.raises(runner.QualificationError, match="public v6"):
        runner.analyze_opencode_events(_events(unsafe), expected_task_binding=_TASK_BINDING)

    error = _events(_tool_output()) + b'{"type":"error","error":"provider failed"}\n'
    with pytest.raises(runner.QualificationError, match="error event"):
        runner.analyze_opencode_events(error, expected_task_binding=_TASK_BINDING)


def test_analyzer_classifies_native_projection_and_final_json_separately() -> None:
    native = _event()
    native["part"]["state"]["output"] = "not-json"  # type: ignore[index]
    rows = [native, *_events(_tool_output()).splitlines()[1:]]
    with pytest.raises(runner.QualificationError, match="native Provider projection"):
        runner.analyze_opencode_events(
            b"\n".join(
                row
                if isinstance(row, bytes)
                else json.dumps(row, separators=(",", ":")).encode("utf-8")
                for row in rows
            )
            + b"\n",
            expected_task_binding=_TASK_BINDING,
        )

    events = _events(_tool_output()).splitlines()
    final = json.loads(events[-1])
    final["part"]["text"] = "not-json"
    events[-1] = json.dumps(final, separators=(",", ":")).encode("utf-8")
    with pytest.raises(runner.QualificationError, match="not a JSON object"):
        runner.analyze_opencode_events(
            b"\n".join(events) + b"\n", expected_task_binding=_TASK_BINDING
        )

    final["part"]["text"] = pass13_evidence.canonical_json(
        {"summary": "only one field"}
    )
    events[-1] = json.dumps(final, separators=(",", ":")).encode("utf-8")
    with pytest.raises(runner.QualificationError, match="required field"):
        runner.analyze_opencode_events(
            b"\n".join(events) + b"\n", expected_task_binding=_TASK_BINDING
        )


def test_analyzer_requires_the_exact_task_binding() -> None:
    wrong = dict(_TASK_BINDING)
    wrong["binding_sha256"] = "f" * 64
    with pytest.raises(runner.QualificationError, match="public v6"):
        runner.analyze_opencode_events(
            _events(_tool_output()), expected_task_binding=wrong
        )


def _model_observation(*, summary: bool, mode: str | None = None) -> dict[str, object]:
    return {
        "schema_version": "deeplaw.opencode-model-observation/v1",
        "event_type": "message.updated",
        "session_sha256": hashlib.sha256(b"session-fixture").hexdigest(),
        "message_sha256": "a" * 64,
        "role": "assistant",
        "provider_id": "deepseek",
        "model_id": "deepseek-v4-flash",
        "summary": summary,
        "mode": mode,
        "finish": "stop",
        "tokens": {
            "input": 10,
            "cache": {"read": 2, "write": 0},
            "output": 4,
            "reasoning": 1,
            "total": 17,
        },
    }


def test_actual_assistant_response_model_comes_from_plugin_metadata() -> None:
    observations = [_model_observation(summary=False)]
    assert runner._response_model_from_observations(
        observations,
        session_id="session-fixture",
    ) == (
        "deepseek",
        "deepseek-v4-flash",
        1,
    )

    with pytest.raises(runner.QualificationError, match="model identity"):
        runner._response_model_from_observations([], session_id="session-fixture")
    observations[0]["model_id"] = "deepseek-chat"
    with pytest.raises(runner.QualificationError, match="model identity"):
        runner._response_model_from_observations(
            observations,
            session_id="session-fixture",
        )


def test_error_tool_event_validates_call_shape_before_status() -> None:
    event = _event()
    event["part"]["state"]["status"] = "error"  # type: ignore[index]
    event["part"]["state"]["input"]["task_binding"] = {  # type: ignore[index]
        **_TASK_BINDING,
        "binding_sha256": "f" * 64,
    }
    with pytest.raises(runner.QualificationError, match="public v6"):
        runner.analyze_opencode_events(
            (json.dumps(event, separators=(",", ":")) + "\n").encode("utf-8"),
            expected_task_binding=_TASK_BINDING,
        )

    event["part"]["state"]["input"]["task_binding"] = _TASK_BINDING  # type: ignore[index]
    with pytest.raises(runner.QualificationError, match="did not complete"):
        runner.analyze_opencode_events(
            (json.dumps(event, separators=(",", ":")) + "\n").encode("utf-8"),
            expected_task_binding=_TASK_BINDING,
        )


def test_compaction_usage_comes_from_one_sanitized_metadata_observation() -> None:
    observations = [_model_observation(summary=True, mode="compaction")]
    usage = runner._compaction_usage_from_observations(
        observations,
        session_id="session-fixture",
    )
    assert usage["total_tokens"] == 17
    aggregate, native_turn = runner._account_turn_usage(usage, usage)
    assert aggregate["total_tokens"] == 34
    assert native_turn["total_tokens"] == 17
    with pytest.raises(runner.QualificationError, match="one actual compaction"):
        runner._compaction_usage_from_observations(
            [],
            session_id="session-fixture",
        )


def test_machine_markers_require_the_final_decision_and_post_forget_gap() -> None:
    case = runner.pass16_continuity_cases.task_case("compaction_forget")
    current = case["current_checkpoint"]
    analysis = {
        "provider_values": [
            {
                "decision": current["decision"],
                "next_action": current["next_action"],
                "marker": current["marker"],
            }
        ],
        "final_value": {
            "summary": "bounded",
            "next_step": current["next_action"],
            "preserved_decisions": [current["decision"]],
            "open_gaps": [],
        },
        "safe_read": {"provider_payloads": [{"gap_count": 0}]},
    }
    before = runner._marker_check(analysis, case=case)
    assert before["expected_decision"] is True
    assert before["expected_next_action"] is True
    assert before["forbidden_admission_count"] == 0

    analysis["final_value"] = {
        "summary": "gap after forget",
        "next_step": current["next_action"],
        "preserved_decisions": [],
        "open_gaps": ["No current checkpoint is admitted."],
    }
    analysis["provider_values"] = [{"gaps": [{"code": "insufficient_context"}]}]
    analysis["safe_read"] = {"provider_payloads": [{"gap_count": 1}]}
    after = runner._marker_check(analysis, case=case, post_forget=True)
    assert after["forgotten_admission_count"] == 0
    assert after["expected_state_absent"] is True
    assert after["gap_observed"] is True


def test_token_arithmetic_and_ledger_mutation_fail_closed() -> None:
    with pytest.raises(runner.QualificationError, match="token"):
        runner.validate_token_usage(
            {
                "input_tokens": 10,
                "cached_input_tokens": 2,
                "cache_write_input_tokens": 0,
                "output_tokens": 4,
                "reasoning_output_tokens": 1,
                "total_tokens": 16,
            }
        )
    assert runner.validate_ledger_heads("a" * 64, "a" * 64) is True
    with pytest.raises(runner.QualificationError, match="ledger"):
        runner.validate_ledger_heads("a" * 64, "b" * 64)


def test_ledger_head_binds_knowledge_and_autonomous_audits(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    calls: list[list[object]] = []
    heads = iter(("a" * 64, "b" * 64))

    def run(arguments, **kwargs):
        del kwargs
        calls.append(arguments)
        return {
            "returncode": 0,
            "stdout": json.dumps({"audit_head": next(heads)}).encode("utf-8"),
            "stderr": b"",
            "timed_out": False,
            "output_overflow": False,
        }

    monkeypatch.setattr(runner, "_run_bounded_process", run)
    observed = runner._ledger_head(
        tmp_path / "deeplaw",
        tmp_path / "vault",
        environment={"PATH": os.defpath},
        cwd=tmp_path,
    )
    expected = hashlib.sha256(
        runner._encoded({"autonomous": "b" * 64, "knowledge": "a" * 64})
    ).hexdigest()
    assert observed == expected
    assert [call[2] for call in calls] == ["inspect", "autonomy"]


def test_source_forget_receipt_requires_retention_and_withdrawal() -> None:
    receipt = {
        "target_type": "source_revision",
        "current_retrieval_eligible": False,
        "current_admission_eligible": False,
        "original_bytes_retained": True,
        "history_retained": True,
        "audit_history_retained": True,
        "bytes_deleted": False,
        "canonical_bytes_deleted": False,
        "message": "withdrawn from current admission; bytes retained; audit history retained",
    }
    assert runner.validate_source_forget_receipt(receipt) is True
    receipt["bytes_deleted"] = True
    with pytest.raises(runner.QualificationError, match="retention"):
        runner.validate_source_forget_receipt(receipt)


def test_report_validation_requires_three_runs_and_path_free_artifacts(tmp_path: Path) -> None:
    orchestrator = runner.QualificationOrchestrator(
        host="opencode",
        repository=Path(__file__).resolve().parents[1],
        candidate_wheel=tmp_path / "candidate.whl",
        deeplaw_executable=tmp_path / "deeplaw",
        output_dir=tmp_path / "evidence",
        error_type=runner.QualificationError,
    )
    with pytest.raises(runner.QualificationError):
        orchestrator.build_report(
            binding={},
            environment={},
            host_attestation={},
            tool_schema={},
            runs=[],
            lifecycle={},
            security={},
            not_executed=["resume", "fork"],
        )


def test_retained_artifact_scans_before_write_and_manifest_excludes_itself(tmp_path: Path) -> None:
    artifact = tmp_path / "events.jsonl"
    runner.retain_artifact(
        artifact,
        b'{"status":"passed"}\n',
        output_root=tmp_path,
        forbidden_values=("qualification-secret",),
    )
    with pytest.raises(runner.QualificationError, match="forbidden"):
        runner.retain_artifact(
            tmp_path / "bad.json",
            b'{"value":"qualification-secret"}\n',
            output_root=tmp_path,
            forbidden_values=("qualification-secret",),
        )
    role_paths = {
        "qualification_report": artifact,
        "sanitized_events_run_1": tmp_path / "run-1.jsonl",
        "sanitized_events_run_2": tmp_path / "run-2.jsonl",
        "sanitized_events_run_3": tmp_path / "run-3.jsonl",
        "preflight_receipt": tmp_path / "preflight.json",
    }
    for path in list(role_paths.values())[1:]:
        path.write_text('{"status":"passed"}\n', encoding="utf-8")
    orchestrator = runner.QualificationOrchestrator(
        host="opencode",
        repository=Path(__file__).resolve().parents[1],
        candidate_wheel=tmp_path / "candidate.whl",
        deeplaw_executable=tmp_path / "deeplaw",
        output_dir=tmp_path,
        error_type=runner.QualificationError,
    )
    manifest = orchestrator.finalize_bundle(
        commit="a" * 40,
        tree="b" * 40,
        artifacts=role_paths,
    )
    assert "bundle-manifest" not in {row["name"] for row in manifest["artifacts"]}
    assert str(tmp_path) not in json.dumps(manifest)


def test_execute_success_cleans_external_isolated_root(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    created: list[Path] = []

    def fake_body(**kwargs: object) -> dict[str, str]:
        root = kwargs["root"]
        assert isinstance(root, Path)
        assert root.parent != kwargs["output_dir"]
        output = kwargs["output_dir"]
        assert isinstance(output, Path)
        output.mkdir(parents=True)
        (root / "opencode.db").write_text("raw session state", encoding="utf-8")
        created.append(root)
        return {"status": "executed"}

    monkeypatch.setattr(runner, "_execute_qualification_body", fake_body)
    output_dir = tmp_path / "retained"
    result = runner.execute_qualification(
        candidate_wheel=tmp_path / "candidate.whl",
        deeplaw_executable=tmp_path / "deeplaw",
        output_dir=output_dir,
        opencode_binary=tmp_path / "opencode",
        host_launcher=tmp_path / "owner-broker",
        human_gold_path=tmp_path / "human-gold.json",
    )
    assert result == {"status": "executed"}
    assert created and not created[0].exists()
    assert output_dir.is_dir()
    assert list(output_dir.iterdir()) == []


def test_execute_failure_cleans_root_and_preserves_original_exception(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    created: list[Path] = []

    def fake_body(**kwargs: object) -> dict[str, str]:
        root = kwargs["root"]
        assert isinstance(root, Path)
        (root / "session-transcript.db").write_text("raw transcript", encoding="utf-8")
        created.append(root)
        raise RuntimeError("original qualification failure")

    monkeypatch.setattr(runner, "_execute_qualification_body", fake_body)
    with pytest.raises(RuntimeError, match="original qualification failure"):
        runner.execute_qualification(
            candidate_wheel=tmp_path / "candidate.whl",
            deeplaw_executable=tmp_path / "deeplaw",
            output_dir=tmp_path / "retained-failure",
            opencode_binary=tmp_path / "opencode",
            host_launcher=tmp_path / "owner-broker",
            human_gold_path=tmp_path / "human-gold.json",
        )
    assert created and not created[0].exists()


def test_cleanup_failure_is_explicit_without_swallowing_original(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    root = Path(runner.tempfile.mkdtemp(prefix=runner._ISOLATED_ROOT_PREFIX))

    def fail_cleanup(_: Path) -> None:
        raise runner.QualificationError("cleanup backend unavailable")

    monkeypatch.setattr(runner, "_cleanup_isolated_root", fail_cleanup)
    original = RuntimeError("original failure")
    runner._cleanup_after_qualification(root, original)
    assert any("SECURITY" in note for note in getattr(original, "__notes__", []))
    assert root.exists()
    runner.shutil.rmtree(root)


def test_cleanup_rejects_non_runner_owned_target(tmp_path: Path) -> None:
    with pytest.raises(runner.QualificationError, match="runner-owned"):
        runner._cleanup_isolated_root(tmp_path)


@pytest.mark.parametrize("status", ["partial", "failed"])
def test_main_returns_nonzero_for_nonexecuted_report(
    status: str,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setattr(
        runner,
        "execute_qualification",
        lambda **_kwargs: {"status": status},
    )
    assert runner.main(
        [
            "--candidate-wheel",
            str(tmp_path / "candidate.whl"),
            "--deeplaw-executable",
            str(tmp_path / "deeplaw"),
            "--output-dir",
            str(tmp_path / "output"),
            "--opencode-binary",
            str(tmp_path / "opencode"),
            "--opencode-launcher",
            str(tmp_path / "owner-broker"),
            "--human-gold",
            str(tmp_path / "human-gold.json"),
        ]
    ) == 1
    assert tmp_path.exists()

    escaped = tmp_path / f"{runner._ISOLATED_ROOT_PREFIX}fixture"
    escaped.mkdir()
    with pytest.raises(runner.QualificationError, match="escaped"):
        runner._cleanup_isolated_root(escaped)
    assert escaped.exists()
