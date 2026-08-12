from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path

import pytest

from benchmarks.hosts import pass13_evidence
from benchmarks.hosts import run_pass13_opencode_continuity_qualification as runner


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
                },
                "output": pass13_evidence.canonical_json(selected),
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
                    "text": pass13_evidence.canonical_json({"summary": "bounded"})
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


def test_secret_parser_is_exact_and_never_accepts_ambient_or_duplicates(tmp_path: Path) -> None:
    dotenv = tmp_path / ".env"
    dotenv.write_text("# comment\nDEEPSEEK_API_KEY='qualification-secret'\n", encoding="utf-8")
    assert runner.load_deepseek_key(dotenv) == "qualification-secret"

    dotenv.write_text("DEEPSEEK_API_KEY=one\nDEEPSEEK_API_KEY=two\n", encoding="utf-8")
    with pytest.raises(ValueError, match="invalid"):
        runner.load_deepseek_key(dotenv)

    dotenv.write_text("OTHER=ambient\n", encoding="utf-8")
    with pytest.raises(ValueError, match="invalid"):
        runner.load_deepseek_key(dotenv)


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
    assert set(config["mcp"]) == {"deeplaw_knowledge"}  # type: ignore[arg-type]


def test_host_environment_is_allowlisted_and_isolated(tmp_path: Path) -> None:
    environment = runner.build_host_environment(
        root=tmp_path,
        opencode_binary=tmp_path / "bin" / "opencode",
        node_binary=tmp_path / "bin" / "node",
        provider_key="qualification-secret",
        canaries={name: f"canary-{index}" for index, name in enumerate(runner._CANARY_NAMES)},
    )
    assert environment["DEEPSEEK_API_KEY"] == "qualification-secret"
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
        "child_argv": ["deeplaw", "knowledge", "mcp", "--stdio", "--vault", "vault"],
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
    with pytest.raises(runner.QualificationError, match="unexpected event"):
        runner.parse_availability_result(
            stdout=_events(_tool_output()), returncode=0, elapsed_ms=1
        )


def test_analyzer_accepts_one_or_two_safe_reads_and_rejects_three() -> None:
    one = runner.analyze_opencode_events(_events(_tool_output()))
    assert one["safe_read"]["call_count"] == 1  # type: ignore[index]
    assert one["usage"]["total_tokens"] == 17  # type: ignore[index]

    two = runner.analyze_opencode_events(
        _events(_insufficient_output(), _tool_output(marker="SECOND"))
    )
    assert two["safe_read"]["call_count"] == 2  # type: ignore[index]
    assert two["safe_read"]["bounded_retry_used"] is True  # type: ignore[index]

    with pytest.raises(runner.QualificationError, match="one or two"):
        runner.analyze_opencode_events(_events(_tool_output(), _tool_output(), _tool_output()))


def test_analyzer_rejects_provider_canonical_mismatch_and_unsafe_operation() -> None:
    mismatched = _tool_output()
    mismatched["content"][0]["text"] = json.dumps(_capsule())  # type: ignore[index]
    with pytest.raises(runner.QualificationError, match="canonical"):
        runner.analyze_opencode_events(_events(mismatched))

    unsafe = _tool_output(operation="semantic")
    with pytest.raises(runner.QualificationError, match="safe context"):
        runner.analyze_opencode_events(_events(unsafe))

    error = _events(_tool_output()) + b'{"type":"error","error":"provider failed"}\n'
    with pytest.raises(runner.QualificationError, match="error event"):
        runner.analyze_opencode_events(error)


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


def test_report_validation_requires_three_runs_and_path_free_artifacts() -> None:
    report = runner.build_skeleton_report(
        commit="a" * 40,
        tree="b" * 40,
        wheel_name="deeplaw-0.12.0-py3-none-any.whl",
        wheel_sha256="c" * 64,
        wheel_bytes=1,
        runtime_executable_sha256="d" * 64,
        contract_digests={
            "host-continuity-qualification.v1.schema.json": "e" * 64,
            "host-qualification-bundle-manifest.v1.schema.json": "f" * 64,
            "provider-knowledge-capsule.v2.schema.json": "0" * 64,
        },
        runs=[],
        not_executed=["resume", "fork"],
    )
    with pytest.raises(runner.QualificationError, match="three"):
        runner.validate_report(report)


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
    manifest = runner.make_bundle_manifest(
        output_dir=tmp_path,
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
        dotenv=tmp_path / ".env",
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
            dotenv=tmp_path / ".env",
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
    assert tmp_path.exists()

    escaped = tmp_path / f"{runner._ISOLATED_ROOT_PREFIX}fixture"
    escaped.mkdir()
    with pytest.raises(runner.QualificationError, match="escaped"):
        runner._cleanup_isolated_root(escaped)
    assert escaped.exists()
