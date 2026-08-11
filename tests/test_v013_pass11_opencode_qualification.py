from __future__ import annotations

import json
from pathlib import Path

import pytest
from jsonschema import Draft202012Validator

from benchmarks.hosts import run_opencode_continuity_qualification as qualification
from deeplaw.util import canonical_json

REPOSITORY = Path(__file__).resolve().parents[1]
SCHEMA = REPOSITORY / "contracts/opencode-continuity-observation.v1.schema.json"
CANDIDATE = (
    REPOSITORY
    / "benchmarks/v013/qualification/candidate/continuity-task-suite-v1.json"
)


def _event(event_type: str, part: dict[str, object]) -> bytes:
    return canonical_json(
        {
            "type": event_type,
            "timestamp": 1,
            "sessionID": "session-synthetic",
            "part": part,
        }
    ).encode("utf-8")


def _synthetic_events(*, tool: str = "deeplaw_knowledge_knowledge_support") -> bytes:
    provider = {
        "schema_version": "deeplaw.knowledge-support-output/v6",
        "result": {
            "provider_capsule": {
                "schema_version": "deeplaw.provider-knowledge-capsule/v2",
                "capsule": {"statements": [], "gaps": [{"code": "no_answer"}]},
                "delivery": {"write_performed": False},
            }
        },
    }
    final = {
        "summary": "Synthetic current state.",
        "next_step": "Freeze the harness.",
        "preserved_decisions": ["Keep package 0.12.0."],
        "open_gaps": ["Qualification holdout."],
        "artifact_refs": ["commit:synthetic"],
    }
    values = [
        _event(
            "step_start",
            {
                "id": "part-start",
                "sessionID": "session-synthetic",
                "messageID": "message-synthetic",
                "type": "step-start",
            },
        ),
        _event(
            "tool_use",
            {
                "id": "part-tool",
                "sessionID": "session-synthetic",
                "messageID": "message-synthetic",
                "type": "tool",
                "callID": "call-synthetic",
                "tool": tool,
                "state": {
                    "status": "completed",
                    "input": {"operation": "context"},
                    "output": canonical_json(provider),
                    "title": "knowledge_support",
                    "metadata": {},
                    "time": {"start": 1, "end": 2},
                },
            },
        ),
        _event(
            "step_finish",
            {
                "id": "part-finish",
                "sessionID": "session-synthetic",
                "messageID": "message-synthetic",
                "type": "step-finish",
                "reason": "stop",
                "cost": 0.001,
                "tokens": {
                    "total": 19,
                    "input": 12,
                    "output": 4,
                    "reasoning": 3,
                    "cache": {"read": 2, "write": 1},
                },
            },
        ),
        _event(
            "text",
            {
                "id": "part-text",
                "sessionID": "session-synthetic",
                "messageID": "message-synthetic",
                "type": "text",
                "text": canonical_json(final),
                "time": {"start": 2, "end": 3},
            },
        ),
    ]
    return b"\n".join(values) + b"\n"


def test_dotenv_parser_selects_only_deepseek_key_without_ambient_fallback(
    tmp_path: Path,
) -> None:
    path = tmp_path / ".env"
    path.write_text(
        "# synthetic only\nUNRELATED_TOKEN=must-not-be-parsed\n"
        "DEEPSEEK_API_KEY=synthetic-provider-key\n",
        encoding="utf-8",
    )
    assert qualification._load_deepseek_key(path) == "synthetic-provider-key"

    for content in (
        "DEEPSEEK_API_KEY=one\nDEEPSEEK_API_KEY=two\n",
        "export DEEPSEEK_API_KEY=synthetic\n",
        "DEEPSEEK_API_KEY=${OTHER_SECRET}\n",
        "DEEPSEEK_API_KEY=\n",
    ):
        path.write_text(content, encoding="utf-8")
        with pytest.raises(ValueError, match="DeepSeek credential file is invalid"):
            qualification._load_deepseek_key(path)

    target = tmp_path / "target"
    target.write_text("DEEPSEEK_API_KEY=synthetic\n", encoding="utf-8")
    path.unlink()
    path.symlink_to(target)
    with pytest.raises(ValueError, match="DeepSeek credential file is invalid"):
        qualification._load_deepseek_key(path)


def test_config_and_prompt_are_closed_and_contamination_free() -> None:
    fixture = qualification._candidate_fixture(CANDIDATE)
    permission = {"*": "deny", qualification.TOOL_NAME: "allow"}
    config = qualification._opencode_config()
    rendered = canonical_json(config)

    assert config["model"] == "deepseek/deepseek-v4-flash"
    assert config["share"] == "disabled"
    assert config["autoupdate"] is False
    assert config["snapshot"] is False
    assert config["plugin"] == []
    assert config["instructions"] == []
    assert config["enabled_providers"] == ["deepseek"]
    assert config["permission"] == permission
    assert config["agent"]["qualification"]["permission"] == permission
    assert config["subagent_depth"] == 0
    assert list(config["mcp"]) == ["deeplaw_knowledge"]
    assert config["provider"]["deepseek"]["options"]["apiKey"] == (
        "{env:DEEPSEEK_API_KEY}"
    )
    assert "knowledge_id" not in rendered
    assert "expected_first_action" not in rendered
    assert "scorer" not in rendered.casefold()

    prompt = qualification._prompt(fixture, qualification._task_binding(fixture["target_route"]))
    assert fixture["task"] in prompt
    assert "knowledge_id" not in prompt
    assert "expected_first_action" not in prompt


def test_host_environment_is_a_closed_allowlist(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("UNRELATED_AMBIENT_SECRET", "ambient-secret")
    environment = qualification._host_environment(
        root=tmp_path,
        opencode_binary=tmp_path / "bin" / "opencode",
        node_binary=tmp_path / "node-bin" / "node",
        provider_key="synthetic-provider-key",
        canaries={"DEEPLAW_QUALIFICATION_SECRET_CANARY": "synthetic-canary"},
    )

    assert environment["DEEPSEEK_API_KEY"] == "synthetic-provider-key"
    assert environment["HOME"] == str(tmp_path / "host-home")
    assert environment["OPENCODE_CONFIG"] == str(tmp_path / "opencode.json")
    assert "UNRELATED_AMBIENT_SECRET" not in environment
    assert set(environment) == qualification._EXPECTED_HOST_ENVIRONMENT_NAMES | {
        "DEEPLAW_QUALIFICATION_SECRET_CANARY"
    }


def test_jsonl_analysis_keeps_only_bounded_neutral_receipts() -> None:
    observation = qualification._analyze_events(_synthetic_events())

    assert observation["event_types"] == [
        "step_finish",
        "step_start",
        "text",
        "tool_use",
    ]
    assert observation["usage"] == {
        "status": "provider_reported",
        "input_tokens": 12,
        "cached_input_tokens": 2,
        "cache_write_tokens": 1,
        "output_tokens": 4,
        "reasoning_tokens": 3,
        "total_tokens": 19,
        "cost_usd": 0.001,
    }
    assert observation["tool_calls"][0]["tool"] == qualification.TOOL_NAME
    assert observation["provider_capsule"]["schema_version"] == (
        "deeplaw.provider-knowledge-capsule/v2"
    )
    assert observation["host_output"]["next_step"] == "Freeze the harness."
    assert "text" not in canonical_json(observation["sanitized_events"][0])


def test_jsonl_analysis_fails_closed_on_unproved_or_disallowed_events() -> None:
    missing_usage = b"\n".join(_synthetic_events().splitlines()[:-2]) + b"\n"
    assert qualification._analyze_events(missing_usage)["usage"]["status"] == "unreported"
    assert qualification._analyze_events(_synthetic_events(tool="bash"))["disallowed_tools"] == [
        "bash"
    ]
    reasoning = _event(
        "reasoning",
        {
            "id": "reasoning",
            "sessionID": "session-synthetic",
            "messageID": "message-synthetic",
            "type": "reasoning",
            "text": "must not persist",
            "time": {"start": 1, "end": 2},
        },
    )
    assert qualification._analyze_events(_synthetic_events() + reasoning)[
        "reasoning_event_observed"
    ] is True


def test_formal_observation_schema_is_closed() -> None:
    schema = json.loads(SCHEMA.read_text(encoding="utf-8"))
    Draft202012Validator.check_schema(schema)
    assert schema["additionalProperties"] is False
    assert schema["properties"]["schema_version"]["const"] == (
        "deeplaw.opencode-continuity-observation/v1"
    )
    assert schema["properties"]["release_ready"]["const"] is False
    assert schema["properties"]["claim_eligible"]["const"] is False
    assert schema["properties"]["runs"]["minItems"] == 1
    assert schema["properties"]["runs"]["maxItems"] == 1


def test_no_model_execute_builds_a_valid_clean_report(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    wheel = tmp_path / "candidate.whl"
    wheel.write_bytes(b"synthetic wheel")
    executable = tmp_path / "deeplaw"
    executable.write_text("synthetic", encoding="utf-8")
    opencode = tmp_path / "opencode"
    opencode.write_text("synthetic", encoding="utf-8")
    node = tmp_path / "node"
    node.write_text("synthetic", encoding="utf-8")
    tarball = tmp_path / "package.tgz"
    tarball.write_bytes(b"synthetic package")
    dotenv = tmp_path / ".env"
    dotenv.write_text("DEEPSEEK_API_KEY=synthetic-provider-key\n", encoding="utf-8")
    output = tmp_path / "candidate-output"

    monkeypatch.setattr(
        qualification,
        "repository_binding",
        lambda _repository: {
            "commit": "a" * 40,
            "tree": "b" * 40,
            "package_version": "0.12.0",
            "worktree_clean": True,
        },
    )
    real_sha256 = qualification._sha256_file

    def fake_sha256(path: Path) -> str:
        if path == tarball.resolve():
            return qualification.OPENCODE_TARBALL_SHA256
        return real_sha256(path)

    monkeypatch.setattr(qualification, "_sha256_file", fake_sha256)
    monkeypatch.setattr(
        qualification,
        "_sha1_file",
        lambda path: qualification.OPENCODE_TARBALL_SHA1
        if path == tarball.resolve()
        else "0" * 40,
    )

    def fake_runtime(*, output_dir: Path, deeplaw_executable: Path) -> tuple[Path, str]:
        assert deeplaw_executable == executable
        wrapper = output_dir / "deeplaw-closed-mcp"
        wrapper.write_text("synthetic", encoding="utf-8")
        return wrapper, "c" * 64

    monkeypatch.setattr(qualification, "_prepare_runtime", fake_runtime)
    monkeypatch.setattr(
        qualification,
        "_preflight_opencode",
        lambda **_kwargs: {
            "status": "passed",
            "version": qualification.OPENCODE_VERSION,
            "model": qualification.MODEL,
            "variant": qualification.VARIANT,
            "managed_config_loaded": False,
            "user_global_config_loaded": False,
            "organization_config_loaded": False,
            "external_plugins_loaded": False,
            "resolved_configuration_sha256": "d" * 64,
        },
    )

    provider_capsule = {
        "schema_version": "deeplaw.provider-knowledge-capsule/v2",
        "capsule": {"statements": [], "gaps": [{"code": "no_answer"}]},
        "delivery": {"write_performed": False},
    }
    host_output = {
        "summary": "Synthetic current state.",
        "next_step": "Freeze the harness.",
        "preserved_decisions": ["Keep package 0.12.0."],
        "open_gaps": ["Qualification holdout."],
        "artifact_refs": ["commit:synthetic"],
    }
    usage = {
        "status": "provider_reported",
        "input_tokens": 12,
        "cached_input_tokens": 2,
        "cache_write_tokens": 1,
        "output_tokens": 4,
        "reasoning_tokens": 3,
        "total_tokens": 19,
        "cost_usd": 0.001,
    }

    def fake_run_once(**kwargs: object) -> dict[str, object]:
        run_output = kwargs["output_dir"]
        assert isinstance(run_output, Path)
        events = run_output / "opencode-run-1-events.sanitized.jsonl"
        events.write_text('{"type":"step_finish"}\n', encoding="utf-8")
        return {
            "run_index": 1,
            "status": "passed",
            "exit_status": 0,
            "latency_ms": 10,
            "prompt_sha256": "e" * 64,
            "actual_event_receipt": {
                "stdout_sha256": "f" * 64,
                "stdout_bytes": 10,
                "stderr_sha256": "0" * 64,
                "stderr_bytes": 0,
                "sanitized_events_name": events.name,
                "sanitized_events_sha256": real_sha256(events),
                "sanitized_event_types": ["step_finish", "text", "tool_use"],
                "invalid_event_lines": 0,
                "unknown_event_types": [],
                "tool_calls": [
                    {
                        "tool": qualification.TOOL_NAME,
                        "status": "completed",
                        "input_sha256": "1" * 64,
                        "output_sha256": "2" * 64,
                        "output_bytes": 100,
                    }
                ],
                "final_response_sha256": "3" * 64,
            },
            "usage": usage,
            "environment_receipt": {
                "schema_version": "deeplaw.closed-mcp-environment-receipt/v1"
            },
            "host_output": host_output,
            "provider_capsule": provider_capsule,
            "provider_internal_surface_leak": False,
            "provider_bytes": 100,
            "ledger_audit_head_before": "4" * 64,
            "ledger_audit_head_after": "4" * 64,
            "ledger_unchanged": True,
            "secret_leak": False,
            "absolute_path_leak": False,
            "failure_class": None,
            "failure_summary": None,
        }

    monkeypatch.setattr(qualification, "_run_once", fake_run_once)
    report = qualification.execute(
        fixture_path=CANDIDATE,
        candidate_wheel=wheel,
        deeplaw_executable=executable,
        output_dir=output,
        opencode_command=opencode,
        node_command=node,
        package_tarball=tarball,
        dotenv_path=dotenv,
    )

    assert report["status"] == "executed"
    assert report["release_ready"] is False
    assert report["claim_eligible"] is False
    assert report["host"]["model"] == "deepseek/deepseek-v4-flash"
    assert report["host"]["host_environment_names"] == sorted(
        qualification._EXPECTED_HOST_ENVIRONMENT_NAMES
        | set(qualification._CANARY_NAMES)
    )
    assert not (output / "xdg-data").exists()
    assert (output / "opencode-continuity-observation.json").is_file()
    assert "synthetic-provider-key" not in canonical_json(report)
