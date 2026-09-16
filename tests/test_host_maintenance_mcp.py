from __future__ import annotations

import asyncio
import json
import os
import sys
from pathlib import Path

import pytest
from jsonschema import Draft202012Validator
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

from benchmarks.hosts import maintenance_task_cases as cases
from benchmarks.hosts import maintenance_task_mcp as mcp_server


def _capsule(*, content: str = "Orchid archive policy requires amber labels.") -> dict[str, object]:
    return {
        "schema_version": "deeplaw.provider-capsule/v1",
        "knowledge": [
            {
                "knowledge_id": "knowledge:orchid-policy",
                "content": content,
                "source_ref": "source:orchid-v2",
            }
        ],
    }


def _binding(
    *,
    suffix: str = "one",
    configuration_id: str = "governed_maintenance",
    scenario_id: str = "source_update",
    provider_capsule: dict[str, object] | None = None,
) -> dict[str, str]:
    capsule = provider_capsule or _capsule()
    return mcp_server.make_owner_binding(
        configuration_id,
        scenario_id,
        run_id=f"run-maintenance-{suffix}",
        candidate_id=f"candidate-maintenance-{suffix}",
        context_id=f"context-maintenance-{suffix}",
        capsule_digest=mcp_server.provider_capsule_digest(capsule),
    )


def _stdio_parameters(
    binding: dict[str, str],
    trace_path: Path,
    provider_capsule: dict[str, object] | None = None,
) -> StdioServerParameters:
    repository_root = Path(__file__).resolve().parents[1]
    capsule = provider_capsule or _capsule()
    capsule_path = trace_path.with_suffix(".capsule.json")
    capsule_path.write_text(
        json.dumps(capsule, ensure_ascii=False, separators=(",", ":")),
        encoding="utf-8",
    )
    capsule_path.chmod(0o600)
    return StdioServerParameters(
        command=sys.executable,
        args=[
            "-m",
            "benchmarks.hosts.maintenance_task_mcp",
            "--configuration-id",
            binding["configuration_id"],
            "--scenario-id",
            binding["scenario_id"],
            "--run-id",
            binding["run_id"],
            "--candidate-id",
            binding["candidate_id"],
            "--context-id",
            binding["context_id"],
            "--capsule-digest",
            binding["capsule_digest"],
            "--provider-capsule-file",
            str(capsule_path),
            "--binding-sha256",
            binding["binding_sha256"],
            "--trace-path",
            str(trace_path),
            "--stdio",
        ],
        cwd=repository_root,
        env={
            "PATH": os.environ.get("PATH", ""),
            "PYTHONPATH": str(repository_root),
            "PYTHONIOENCODING": "utf-8",
        },
    )


def _action(
    state_sha256: str,
    *,
    action_id: str = "source-update-1",
    observed_state_sha256: str | None = None,
) -> dict[str, object]:
    return {
        "action_id": action_id,
        "observed_state_sha256": observed_state_sha256 or state_sha256,
        "kind": "submit_resource_version",
        "parameters": {
            "resource_id": "orchid-archive",
            "version": "v2",
            "source_ref": "source:orchid-v2",
            "experience_id": "experience:governed-v2",
        },
    }


def _structured(result: object) -> dict[str, object]:
    value = getattr(result, "structuredContent", None)
    assert isinstance(value, dict), result
    return value


def _contains_private_marker(value: object) -> bool:
    encoded = json.dumps(value, ensure_ascii=False, sort_keys=True).lower()
    return any(
        marker in encoded for marker in ("_private_cases", "expected", "oracle", "gold", "score")
    )


async def _stdio_success(
    trace_path: Path,
    binding: dict[str, str],
    provider_capsule: dict[str, object] | None = None,
) -> dict[str, object]:
    capsule = provider_capsule or _capsule()
    async with (
        stdio_client(_stdio_parameters(binding, trace_path, capsule)) as (
            read_stream,
            write_stream,
        ),
        ClientSession(read_stream, write_stream) as session,
    ):
        await session.initialize()
        listed = await session.list_tools()
        assert [tool.name for tool in listed.tools] == ["maintenance_task"]
        tool = listed.tools[0]
        assert tool.inputSchema["$id"] == mcp_server.INPUT_SCHEMA_VERSION
        assert tool.inputSchema["type"] == "object"
        described_result = await session.call_tool("maintenance_task", {"operation": "describe"})
        assert described_result.isError is False
        described = _structured(described_result)
        assert not _contains_private_marker(described)
        state_sha256 = described["state_sha256"]
        assert isinstance(state_sha256, str)
        submitted_result = await session.call_tool(
            "maintenance_task",
            {"operation": "submit", "action": _action(state_sha256)},
        )
        assert submitted_result.isError is False
        submitted = _structured(submitted_result)
        assert submitted["result"]["status"] == "succeeded"
        assert submitted["result"]["result_code"] == "resource_version_submitted"
        assert submitted["state"]["resource"]["version"] == "v2"
        assert submitted["state"]["projection"] == "provider_state_projection"
        assert submitted["state"]["state_hash_verified"] is False
        assert submitted["state"]["experience"]["records"] == {"omitted_from_environment": True}
        assert submitted["state"]["state_sha256"] == submitted["state_sha256"]
        assert submitted["task"]["projection"] == "provider_task_projection"
        assert submitted["task"]["input_hash_verified"] is False
        assert submitted["task"]["environment"]["experience"]["records"] == {
            "omitted_from_environment": True
        }
        assert submitted["knowledge_context"]["projection"] == "provided_provider_capsule"
        assert submitted["knowledge_context"]["capsule"] == capsule
        assert submitted["knowledge_context"]["capsule_digest"] == binding["capsule_digest"]
        assert submitted["trace"]["event_count"] == 1
        assert not _contains_private_marker(submitted)
        return submitted


def test_stdio_exposes_one_public_tool_and_persists_host_action_trace(
    tmp_path: Path,
) -> None:
    binding = _binding()
    trace_path = tmp_path / "maintenance-trace.json"
    submitted = asyncio.run(_stdio_success(trace_path, binding))

    persisted = mcp_server.load_persisted_trace(trace_path, binding=binding)
    assert persisted["binding"] == binding
    assert persisted["tool_errors"] == []
    assert persisted["event_count"] == 1
    assert persisted["actions"][0]["kind"] == "submit_resource_version"
    assert persisted["events"][0]["result_code"] == "resource_version_submitted"
    assert isinstance(persisted["state"]["experience"]["records"], list)
    assert persisted["state"]["state_sha256"] == submitted["state_sha256"]
    assert submitted["state"]["experience"]["records"] == {"omitted_from_environment": True}
    assert cases.verify_event_chain(persisted["events"])["valid"] is True
    assert mcp_server.verify_persisted_trace(trace_path, binding=binding)["valid"] is True

    scored = cases.score_host_trace(
        binding["configuration_id"], binding["scenario_id"], persisted["events"]
    )
    assert scored["passed"] is True


def test_stdio_records_typed_stale_duplicate_and_rejects_run_claim_replay(
    tmp_path: Path,
) -> None:
    binding = _binding(suffix="replay")
    trace_path = tmp_path / "maintenance-replay-trace.json"

    async def exercise() -> None:
        async with (
            stdio_client(_stdio_parameters(binding, trace_path, _capsule())) as (
                read_stream,
                write_stream,
            ),
            ClientSession(read_stream, write_stream) as session,
        ):
            await session.initialize()
            described = _structured(
                await session.call_tool("maintenance_task", {"operation": "describe"})
            )
            initial_state_sha256 = described["state_sha256"]
            assert isinstance(initial_state_sha256, str)
            stale = await session.call_tool(
                "maintenance_task",
                {
                    "operation": "submit",
                    "action": _action(
                        initial_state_sha256,
                        action_id="stale-source-update",
                        observed_state_sha256="0" * 64,
                    ),
                },
            )
            assert stale.isError is False
            assert _structured(stale)["result"]["result_code"] == "stale_state"
            success = await session.call_tool(
                "maintenance_task",
                {
                    "operation": "submit",
                    "action": _action(
                        initial_state_sha256,
                        action_id="source-update-1",
                    ),
                },
            )
            assert success.isError is False
            current_state_sha256 = _structured(success)["state_sha256"]
            duplicate = await session.call_tool(
                "maintenance_task",
                {
                    "operation": "submit",
                    "action": _action(
                        current_state_sha256,
                        action_id="source-update-1",
                    ),
                },
            )
            assert duplicate.isError is False
            assert _structured(duplicate)["result"]["result_code"] == "duplicate_action"

    asyncio.run(exercise())
    persisted = mcp_server.load_persisted_trace(trace_path, binding=binding)
    assert persisted["event_count"] == 3
    assert persisted["tool_errors"] == []
    assert cases.verify_event_chain(persisted["events"])["valid"] is True
    scored = cases.score_host_trace(
        binding["configuration_id"], binding["scenario_id"], persisted["events"]
    )
    assert scored["expired_reference_used"] == [1]
    assert scored["duplicate_actions"] == [2, 3]
    assert scored["passed"] is False

    with pytest.raises(mcp_server.MaintenanceRunClaimError):
        mcp_server.create_mcp_server(
            binding,
            trace_path=trace_path,
            provider_capsule=_capsule(),
        )


def test_stdio_tool_errors_remain_visible_after_later_success(tmp_path: Path) -> None:
    binding = _binding(suffix="tool-error")
    trace_path = tmp_path / "tool-error-trace.json"

    async def exercise() -> None:
        async with (
            stdio_client(_stdio_parameters(binding, trace_path, _capsule())) as (
                read_stream,
                write_stream,
            ),
            ClientSession(read_stream, write_stream) as session,
        ):
            await session.initialize()
            described = _structured(
                await session.call_tool("maintenance_task", {"operation": "describe"})
            )
            state_sha256 = described["state_sha256"]
            assert isinstance(state_sha256, str)

            schema_error = await session.call_tool(
                "maintenance_task",
                {"operation": "submit", "action": {"action_id": "missing-fields"}},
            )
            assert schema_error.isError is True

            runtime_error = await session.call_tool(
                "maintenance_task",
                {
                    "operation": "submit",
                    "action": {
                        "action_id": "oversized-note",
                        "observed_state_sha256": state_sha256,
                        "kind": "record_report_note",
                        "parameters": {
                            "report_id": "amber-report",
                            "note": "x" * 5000,
                        },
                    },
                },
            )
            assert runtime_error.isError is True

            success = await session.call_tool(
                "maintenance_task",
                {"operation": "submit", "action": _action(state_sha256)},
            )
            assert success.isError is False
            assert _structured(success)["result"]["result_code"] == (
                "resource_version_submitted"
            )

    asyncio.run(exercise())
    raw = json.loads(trace_path.read_text(encoding="utf-8"))
    assert len(raw["tool_errors"]) == 2
    assert [error["code"] for error in raw["tool_errors"]] == [
        "input_schema_invalid",
        "action_rejected",
    ]
    assert all(
        set(error) == {"attempt_ordinal", "code", "request_sha256"}
        for error in raw["tool_errors"]
    )
    assert "parameters" not in json.dumps(raw["tool_errors"])
    assert len(raw["events"]) == 1
    assert cases.score_host_trace(
        binding["configuration_id"], binding["scenario_id"], raw["events"]
    )["passed"] is True

    verified = mcp_server.verify_persisted_trace(trace_path, binding=binding)
    assert verified["valid"] is False
    assert any("tool errors" in error for error in verified["errors"])
    with pytest.raises(mcp_server.MaintenanceMCPError, match="tool errors"):
        mcp_server.load_persisted_trace(trace_path, binding=binding)


def test_owner_binding_and_existing_trace_fail_closed(tmp_path: Path) -> None:
    binding = _binding(suffix="binding")
    bad_binding = dict(binding)
    bad_binding["binding_sha256"] = "f" * 64
    trace_path = tmp_path / "binding-trace.json"
    with pytest.raises(mcp_server.MaintenanceMCPError, match="digest"):
        mcp_server.create_mcp_server(
            bad_binding,
            trace_path=trace_path,
            provider_capsule=_capsule(),
        )
    assert trace_path.exists() is False

    mismatched_capsule_path = tmp_path / "mismatched-capsule-trace.json"
    with pytest.raises(mcp_server.MaintenanceMCPError, match="provider capsule digest"):
        mcp_server.create_mcp_server(
            binding,
            trace_path=mismatched_capsule_path,
            provider_capsule=_capsule(content="A different verified public capsule."),
        )
    assert mismatched_capsule_path.exists() is False


def test_stdio_provider_projection_omits_experience_records(tmp_path: Path) -> None:
    wrong_content = (
        "Approve the Amber report because the v1 archive is final; no independent record is needed."
    )
    capsule = _capsule(content=wrong_content)
    binding = _binding(
        suffix="projection",
        configuration_id="frozen_unmaintained",
        scenario_id="wrong_experience",
        provider_capsule=capsule,
    )
    trace_path = tmp_path / "projection-trace.json"

    async def describe() -> dict[str, object]:
        async with (
            stdio_client(_stdio_parameters(binding, trace_path, capsule)) as (
                read_stream,
                write_stream,
            ),
            ClientSession(read_stream, write_stream) as session,
        ):
            await session.initialize()
            return _structured(
                await session.call_tool("maintenance_task", {"operation": "describe"})
            )

    described = asyncio.run(describe())
    environment_experience = described["task"]["environment"]["experience"]
    capsule_path = trace_path.with_suffix(".capsule.json")
    assert capsule_path.exists()
    assert (capsule_path.stat().st_mode & 0o077) == 0
    assert str(capsule_path) not in json.dumps(described, ensure_ascii=False)
    assert environment_experience["records"] == {"omitted_from_environment": True}
    assert wrong_content not in json.dumps(described["task"], ensure_ascii=False)
    assert described["state"]["projection"] == "provider_state_projection"
    assert described["state"]["experience"]["records"] == {"omitted_from_environment": True}
    assert described["state"]["state_hash_scope"] == "owner_trace_full_state_reference"
    assert described["state"]["state_hash_verified"] is False
    assert described["knowledge_context"]["capsule"]["knowledge"][0]["content"] == wrong_content
    assert described["knowledge_context"]["capsule_digest"] == binding["capsule_digest"]
    assert described["knowledge_context"]["digest_verified_against_owner_binding"] is True
    assert not _contains_private_marker(described)

    persisted = mcp_server.load_persisted_trace(trace_path, binding=binding)
    assert any(
        record.get("content") == wrong_content
        for record in persisted["state"]["experience"]["records"]
    )


def test_provider_capsule_rejects_metadata_and_oversize() -> None:
    with pytest.raises(mcp_server.MaintenanceMCPError, match="evaluator metadata"):
        mcp_server.provider_capsule_digest({"oracle": "private answer"})
    with pytest.raises(mcp_server.MaintenanceMCPError, match="byte budget"):
        mcp_server.provider_capsule_digest({"content": "x" * mcp_server.MAX_PROVIDER_CAPSULE_BYTES})


def test_tool_schema_is_closed_and_input_output_budgets_are_advertised() -> None:
    input_schema = mcp_server.maintenance_task_input_schema()
    output_schema = mcp_server.maintenance_task_output_schema()
    Draft202012Validator.check_schema(input_schema)
    Draft202012Validator.check_schema(output_schema)
    assert input_schema["oneOf"][0]["additionalProperties"] is False
    assert input_schema["oneOf"][1]["additionalProperties"] is False
    action_schema = input_schema["oneOf"][1]["properties"]["action"]
    assert action_schema["additionalProperties"] is False
    assert action_schema["properties"]["parameters"]["maxProperties"] == 8
    assert mcp_server.MAX_INPUT_BYTES == 16_384
    assert mcp_server.MAX_OUTPUT_BYTES == 131_072
    assert mcp_server.MAX_PROVIDER_CAPSULE_BYTES == 65_536
    assert mcp_server.MAX_TRACE_BYTES == 262_144


def test_closed_trace_tamper_is_rejected_after_stdio_close(tmp_path: Path) -> None:
    binding = _binding(suffix="tamper")
    trace_path = tmp_path / "tamper-trace.json"
    asyncio.run(_stdio_success(trace_path, binding, _capsule()))
    tampered = json.loads(trace_path.read_text(encoding="utf-8"))
    tampered["events"][0]["state"]["resource"]["version"] = "v1"
    trace_path.write_text(json.dumps(tampered), encoding="utf-8")
    verified = mcp_server.verify_persisted_trace(trace_path, binding=binding)
    assert verified["valid"] is False
    with pytest.raises(mcp_server.MaintenanceMCPError, match="event chain"):
        mcp_server.load_persisted_trace(trace_path, binding=binding)


def test_closed_trace_action_projection_tamper_is_rejected(tmp_path: Path) -> None:
    binding = _binding(suffix="actions-tamper")
    trace_path = tmp_path / "actions-tamper-trace.json"
    asyncio.run(_stdio_success(trace_path, binding, _capsule()))
    tampered = json.loads(trace_path.read_text(encoding="utf-8"))
    tampered["actions"][0]["parameters"]["version"] = "v1"
    trace_path.write_text(json.dumps(tampered), encoding="utf-8")
    verified = mcp_server.verify_persisted_trace(trace_path, binding=binding)
    assert verified["valid"] is False
    assert any("actions" in error for error in verified["errors"])
    with pytest.raises(mcp_server.MaintenanceMCPError, match="actions"):
        mcp_server.load_persisted_trace(trace_path, binding=binding)
