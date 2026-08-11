from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest
from jsonschema import Draft202012Validator, ValidationError

from benchmarks.hosts import run_codex_token_attribution as attribution
from deeplaw.util import canonical_json

REPOSITORY = Path(__file__).resolve().parents[1]


def _usage(input_tokens: int, cached: int, output: int) -> dict[str, object]:
    return {
        "status": "provider_reported",
        "input_tokens": input_tokens,
        "cached_input_tokens": cached,
        "output_tokens": output,
        "reasoning_output_tokens": 0,
        "total_tokens": input_tokens + output,
    }


def test_full_and_context_only_schemas_are_exact_and_closed() -> None:
    full = attribution.full_input_schema()
    operations = attribution.operation_names(full)
    assert operations == (
        "compilation",
        "context",
        "editor_context",
        "explain",
        "gaps",
        "get",
        "graph",
        "identity_lookup",
        "inspect",
        "lineage",
        "query",
        "recall",
        "search",
        "semantic",
        "source",
        "synthesis",
        "verify",
        "wiki",
        "wiki_lookup",
    )
    narrow = attribution.context_only_input_schema(full)
    assert attribution.operation_names(narrow) == ("context",)
    valid = {
        "operation": "context",
        "task": "Continue the current owner-review task.",
        "confirm_no_case_data": True,
    }
    Draft202012Validator(narrow).validate(valid)
    with pytest.raises(ValidationError):
        Draft202012Validator(narrow).validate(
            {"operation": "source", "task": "wrong", "confirm_no_case_data": True}
        )


def test_schema_receipts_measure_canonical_advertised_bytes() -> None:
    full = attribution.full_input_schema()
    narrow = attribution.context_only_input_schema(full)
    no_tool = attribution.schema_receipt(transport="none", input_schema=None)
    narrow_receipt = attribution.schema_receipt(
        transport="dynamic_tool", input_schema=narrow
    )
    full_receipt = attribution.schema_receipt(
        transport="dynamic_tool", input_schema=full
    )
    assert no_tool["advertised_schema_bytes"] == 0
    assert narrow_receipt["operation_count"] == 1
    assert full_receipt["operation_count"] == 19
    assert full_receipt["advertised_schema_bytes"] > narrow_receipt[
        "advertised_schema_bytes"
    ]
    assert full_receipt["input_schema_sha256"] == attribution.sha256_bytes(
        canonical_json(full).encode("utf-8")
    )


def test_attribution_uses_provider_usage_and_prefrozen_threshold() -> None:
    usages = {
        "A": _usage(1000, 0, 100),
        "B": _usage(1300, 100, 100),
        "C": _usage(1900, 200, 100),
        "D": _usage(2500, 300, 200),
    }
    result = attribution.attribute_tokens(
        usages,
        full_schema_bytes=24_000,
        context_schema_bytes=4_000,
    )
    assert result["C_minus_B"]["input_tokens"] == 600
    assert result["D_minus_C"]["total_tokens"] == 700
    assert result["schema_overhead_significant"] is True
    assert result["profile_change_admitted"] is True

    insignificant = attribution.attribute_tokens(
        {**usages, "C": _usage(1340, 100, 100)},
        full_schema_bytes=24_000,
        context_schema_bytes=4_000,
    )
    assert insignificant["schema_overhead_significant"] is False
    assert insignificant["profile_change_admitted"] is False


def test_missing_usage_never_becomes_zero_or_profile_evidence() -> None:
    missing = {
        "status": "unreported",
        "input_tokens": None,
        "cached_input_tokens": None,
        "output_tokens": None,
        "reasoning_output_tokens": None,
        "total_tokens": None,
    }
    result = attribution.attribute_tokens(
        {
            "A": missing,
            "B": _usage(1300, 100, 100),
            "C": _usage(1900, 200, 100),
            "D": _usage(2500, 300, 200),
        },
        full_schema_bytes=24_000,
        context_schema_bytes=4_000,
    )
    assert result["B_minus_A"]["input_tokens"] is None
    assert result["schema_overhead_significant"] is False
    assert result["profile_change_admitted"] is False


def test_protocol_contract_is_closed_and_keeps_release_false() -> None:
    schema = json.loads(
        (
            REPOSITORY
            / "contracts/codex-token-attribution-observation.v1.schema.json"
        ).read_bytes()
    )
    Draft202012Validator.check_schema(schema)
    assert schema["properties"]["release_ready"] == {"const": False}
    assert schema["properties"]["claim_eligible"] == {"const": False}
    assert schema["properties"]["host"]["properties"]["interface"] == {
        "const": "codex_app_server"
    }


def test_candidate_prompt_has_no_evaluator_labels_or_exact_identity() -> None:
    fixture = attribution.candidate_fixture(
        REPOSITORY
        / "benchmarks/v013/qualification/candidate/continuity-task-suite-v1.json"
    )
    prompt = attribution.candidate_prompt(fixture)
    rendered = prompt.casefold()
    for forbidden in (
        "expected_first_action",
        "expected_decision",
        "expected_marker",
        "first correct action",
        "decision preservation",
        "knowledge_id",
        "revision_id",
        "gold",
        "scorer",
    ):
        assert forbidden not in rendered
    assert "omit null fields" in rendered
    assert "another operation" in rendered
    assert "confirm_no_case_data=true" in rendered


def test_runner_uses_app_server_not_codex_exec() -> None:
    source = (REPOSITORY / "benchmarks/hosts/run_codex_token_attribution.py").read_text(
        encoding="utf-8"
    )
    assert "CodexAppServerClient" in source
    assert '"thread/start"' in source
    assert "codex exec" not in source


def test_app_server_argv_closes_mcp_except_for_exact_d_condition() -> None:
    binary = Path("/Applications/ChatGPT.app/Contents/Resources/codex")
    for condition_id in ("A", "B", "C"):
        argv = attribution._app_server_argv(binary, condition_id)
        assert argv[:3] == [str(binary), "app-server", "--stdio"]
        assert "mcp_servers={}" in argv
        assert not any("mcp_servers.deeplaw" in item for item in argv)
        assert "exec" not in argv

    argv = attribution._app_server_argv(binary, "D")
    assert "mcp_servers={}" in argv
    assert 'mcp_servers.deeplaw.command="./deeplaw-closed-mcp"' in argv
    assert (
        'mcp_servers.deeplaw.args=["knowledge","mcp","--stdio","--vault","vault"]'
        in argv
    )
    assert 'mcp_servers.deeplaw.enabled_tools=["knowledge_support"]' in argv


def test_mcp_result_extraction_prefers_bounded_deeplaw_output() -> None:
    provider = {
        "schema_version": "deeplaw.provider-knowledge-capsule/v2",
        "capsule": {"statements": [], "gaps": []},
    }
    support = {
        "schema_version": "deeplaw.knowledge-support-output/v6",
        "result": {"provider_capsule": provider},
    }
    observed = attribution._knowledge_output_from_value(
        {
            "content": [{"type": "text", "text": canonical_json(support)}],
            "structuredContent": support,
        }
    )
    assert observed == support


def test_execute_assembles_contract_valid_report_without_host(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    wheel = tmp_path / "deeplaw-0.12.0-py3-none-any.whl"
    executable = tmp_path / "runtime" / "bin" / "deeplaw"
    codex = tmp_path / "codex"
    executable.parent.mkdir(parents=True)
    wheel.write_bytes(b"wheel")
    executable.write_bytes(b"runtime")
    codex.write_bytes(b"host")
    provider = {
        "schema_version": "deeplaw.provider-knowledge-capsule/v2",
        "capsule": {"statements": [], "gaps": []},
    }
    ledger = "b" * 64
    monkeypatch.setattr(
        attribution,
        "repository_binding",
        lambda _repository: {
            "commit": "1" * 40,
            "tree": "2" * 40,
            "package_version": "0.12.0",
            "worktree_clean": True,
        },
    )
    monkeypatch.setattr(attribution.shutil, "which", lambda _command: str(codex))
    monkeypatch.setattr(attribution, "_host_environment", lambda *_args: {})
    monkeypatch.setattr(
        attribution.subprocess,
        "run",
        lambda *_args, **_kwargs: SimpleNamespace(
            stdout="codex-cli fixture", stderr="", returncode=0
        ),
    )
    monkeypatch.setattr(attribution, "_confirmed_login_status", lambda _result: True)
    monkeypatch.setattr(
        attribution,
        "_prepare_runtime",
        lambda **_kwargs: (tmp_path / "wrapper", "3" * 64),
    )
    monkeypatch.setattr(
        attribution,
        "_seed_vault",
        lambda *_args: {
            "task_binding": {"binding_sha256": "4" * 64},
            "audit_head": ledger,
        },
    )
    monkeypatch.setattr(
        attribution,
        "_preflight",
        lambda *_args: {"provider_capsule": provider, "ledger_audit_head": ledger},
    )
    monkeypatch.setattr(attribution, "_ledger_head", lambda _vault: ledger)

    def fake_condition(**kwargs: object) -> tuple[dict[str, object], bool, bool]:
        condition_id = str(kwargs["condition_id"])
        condition = attribution._condition_placeholder(condition_id, "replaced")
        condition.update(
            {
                "status": "passed",
                "thread_id_sha256": "5" * 64,
                "turn_id_sha256": "6" * 64,
                "latency_ms": 1,
                "peak_rss_bytes": 1,
                "usage": _usage(1_000 + ord(condition_id), 0, 10),
                "tool_calls": 0 if condition_id == "A" else 1,
                "provider_result_bytes": 0 if condition_id == "A" else 100,
                "host_output": {
                    "summary": "fixture",
                    "next_step": "fixture",
                    "preserved_decisions": [],
                    "open_gaps": [],
                    "artifact_refs": [],
                },
                "provider_capsule": None if condition_id == "A" else provider,
                "event_receipt": {
                    "name": f"codex-token-{condition_id}-events.sanitized.jsonl",
                    "sha256": "7" * 64,
                    "bytes": 0,
                    "methods": ["turn/completed"],
                    "item_types": [],
                    "stderr_sha256": "8" * 64,
                    "stderr_bytes": 0,
                },
                "failure_codes": [],
            }
        )
        if condition_id == "D":
            condition["mcp_environment_receipt"] = {
                "schema_version": "deeplaw.closed-mcp-environment-receipt/v1",
                "closed": True,
                "home_isolated": True,
                "blocked_names_present": [],
                "environment_names": ["HOME", "PATH", "XDG_CONFIG_HOME"],
                "child_argv": [
                    "runtime/bin/python",
                    "runtime/bin/deeplaw",
                    "knowledge",
                    "mcp",
                    "--stdio",
                    "--vault",
                    "vault",
                ],
            }
        return condition, False, False

    monkeypatch.setattr(attribution, "_run_condition", fake_condition)
    report = attribution.execute(
        fixture_path=REPOSITORY
        / "benchmarks/v013/qualification/candidate/continuity-task-suite-v1.json",
        candidate_wheel=wheel,
        deeplaw_executable=executable,
        output_dir=tmp_path / "candidate-output",
        codex_command=str(codex),
    )
    assert report["status"] == "executed"
    assert report["release_ready"] is False
    assert report["claim_eligible"] is False
    attribution._validate_report(report)
