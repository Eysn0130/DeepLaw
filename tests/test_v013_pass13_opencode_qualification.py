from __future__ import annotations

import hashlib
import json
import os
import stat
import subprocess
from contextlib import contextmanager
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from benchmarks.hosts import (
    host_process_receipt_v2,
    pass13_evidence,
)
from benchmarks.hosts import (
    run_pass13_opencode_continuity_qualification as runner,
)
from benchmarks.release.kernel_qualification_bundle_v1 import host_identity_sha256
from deeplaw.task_context import build_task_context_binding

_REAL_INSTALLED_OPENCODE_PLUGIN_BYTES = runner._installed_opencode_plugin_bytes

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

_ZERO_MODEL_CANDIDATE = {
    "commit": "1" * 40,
    "tree": "2" * 40,
    "lock_sha256": "3" * 64,
    "wheel_sha256": "4" * 64,
    "sdist_sha256": "5" * 64,
}
_ZERO_MODEL_RUN_BINDING = {
    "evidence_run_id": 202,
    "qualification_run_id": 303,
}
_ZERO_MODEL_HOST_ITEM = {
    "version": "1.18.16",
    "source_commit": "a3647eb025c7615159d417dcc49fc39fdaeba65b",
    "config_selector": "deepseek/deepseek-v4-flash",
    "expected_response_model_id": "deepseek-v4-flash",
    "executable_sha256": "6" * 64,
    "package_sha256": "7" * 64,
    "runtime": "host_bun_runtime_only",
    "dotenv_policy": "owner_only_external_strict_parser",
    "secret_visibility": "forbidden",
}


def _digest(label: str) -> str:
    return hashlib.sha256(label.encode("utf-8")).hexdigest()


def _zero_model_receipt(*, nonce_sha256: str, now: datetime) -> dict[str, Any]:
    process = _digest("opencode-zero-model-process")
    child = _digest("opencode-zero-model-child")
    native = {
        "event_sequence_sha256": _digest("opencode-zero-model-events"),
        "session_identity_sha256": child,
        "lifecycle_record_sha256": _digest("opencode-zero-model-lifecycle"),
    }
    proof = {
        "proof_kind": "opencode_public_fork_route_correlation",
        "process_identity_sha256": process,
        "request_method": "POST",
        "route_observation_sha256": _digest("actual-public-fork-route"),
        "request_body_sha256": hashlib.sha256(b"{}").hexdigest(),
        "response_sha256": _digest("actual-fork-response"),
        "parent_session_sha256": _digest("actual-ingress-parent"),
        "child_session_sha256": child,
        "child_plugin_event_sha256": _digest("actual-child-plugin-event"),
        "child_plugin_session_sha256": child,
        "native_event_sequence_sha256": native["event_sequence_sha256"],
        "native_session_identity_sha256": native["session_identity_sha256"],
        "native_lifecycle_record_sha256": native["lifecycle_record_sha256"],
        "same_process": True,
        "actual_route_observed": True,
    }
    proof["route_correlation_sha256"] = host_process_receipt_v2.correlation_sha256(
        {
            key: proof[key]
            for key in (
                "process_identity_sha256",
                "request_method",
                "route_observation_sha256",
                "request_body_sha256",
                "response_sha256",
                "parent_session_sha256",
                "child_session_sha256",
                "child_plugin_event_sha256",
                "child_plugin_session_sha256",
                "native_event_sequence_sha256",
                "native_session_identity_sha256",
                "native_lifecycle_record_sha256",
            )
        }
    )
    issued = now - timedelta(seconds=1)
    expires = now + timedelta(seconds=30)
    return host_process_receipt_v2.build_receipt(
        host="opencode",
        task_case="continuity",
        run_id="opencode-zero-model-preflight-202-303",
        candidate_binding=_ZERO_MODEL_CANDIDATE,
        run_binding=_ZERO_MODEL_RUN_BINDING,
        host_binary={"version": "1.18.16", "sha256": "6" * 64},
        broker_source={
            "repository_external": True,
            "owner_only_mode": True,
            "sha256": "8" * 64,
        },
        host_identity_sha256=host_identity_sha256(_ZERO_MODEL_HOST_ITEM),
        host_identity_source_sha256="9" * 64,
        process_identity_sha256=process,
        broker_instance_sha256=_digest("opencode-zero-model-broker-instance"),
        nonce_sha256=nonce_sha256,
        issued_at=issued.strftime("%Y-%m-%dT%H:%M:%SZ"),
        expires_at=expires.strftime("%Y-%m-%dT%H:%M:%SZ"),
        validation_reference_time=now.strftime("%Y-%m-%dT%H:%M:%SZ"),
        selector_source_symlink=True,
        execution_target_regular=True,
        execution_target_single_link=True,
        status="exited",
        exit_code=0,
        native_event_binding=native,
        proof=proof,
        isolation={
            "runner_received_secret": False,
            "mcp_received_secret": False,
            "ambient_auth_forwarded_to_mcp": False,
            "raw_output_retained": False,
        },
    )


def _zero_model_request(*, nonce_sha256: str, now: datetime) -> dict[str, Any]:
    return runner.build_opencode_zero_model_preflight_request(
        task_case="continuity",
        run_id="opencode-zero-model-preflight-202-303",
        candidate_binding=_ZERO_MODEL_CANDIDATE,
        run_binding=_ZERO_MODEL_RUN_BINDING,
        host_binary={"version": "1.18.16", "sha256": "6" * 64},
        broker_source_sha256="8" * 64,
        host_identity_sha256=host_identity_sha256(_ZERO_MODEL_HOST_ITEM),
        host_identity_source_sha256="9" * 64,
        nonce_sha256=nonce_sha256,
        issued_at=(now - timedelta(seconds=2)).strftime("%Y-%m-%dT%H:%M:%SZ"),
        expires_at=(now + timedelta(seconds=60)).strftime("%Y-%m-%dT%H:%M:%SZ"),
    )


def _zero_model_response(*, nonce_sha256: str, now: datetime) -> dict[str, Any]:
    return {
        "schema_version": runner.OPENCODE_BROKER_CONTROL_SCHEMA_VERSION,
        "operation": "zero_model_preflight",
        "status": "observed",
        "observed_sequence": list(runner.OPENCODE_ZERO_MODEL_REQUIRED_SEQUENCE),
        "forbidden_route_count": 0,
        "message_route_count": 0,
        "provider_route_count": 0,
        "model_route_count": 0,
        "mcp_route_count": 0,
        "model_invocation_count": 0,
        "provider_request_count": 0,
        "remote_workspace_forward_count": 0,
        "share_request_count": 0,
        "ambient_plugin_count": 0,
        "event_barrier": {
            "status": "satisfied",
            "response_release": "after_child_plugin_event",
            "timed_out": False,
            "child_plugin_event_count": 1,
            "event_type": "session.created",
            "timeout_seconds": 30,
            "elapsed_ms": 7,
            "parent_source": "actual_ingress_route",
        },
        "host_process_receipt": _zero_model_receipt(
            nonce_sha256=nonce_sha256,
            now=now,
        ),
    }


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


def test_runner_routes_owner_dotenv_only_to_external_broker(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source = Path(runner.__file__).read_text(encoding="utf-8")
    assert not hasattr(runner, "load_deepseek_key")
    assert "--dotenv" not in source
    assert "load_deepseek_key" not in source
    assert "--opencode-dotenv" in source
    assert runner._OWNER_DOTENV_ENV_NAME == "DEEPLAW_OWNER_DOTENV"

    windows_scripts = tmp_path / "runtime" / "Scripts"
    windows_scripts.mkdir(parents=True)
    windows_executable = windows_scripts / "deeplaw.exe"
    windows_executable.write_bytes(b"candidate windows executable")
    windows_python = windows_scripts / "python.exe"
    windows_python.write_bytes(b"candidate windows python")
    calls: list[tuple[Path, ...]] = []

    def fake_run(
        argv: list[Path | str], **_kwargs: object
    ) -> dict[str, object]:
        calls.append(tuple(Path(item) for item in argv))
        return {
            "stdout": b"candidate plugin bytes",
            "stderr": b"",
            "returncode": 0,
            "timed_out": False,
            "output_overflow": False,
        }

    monkeypatch.setattr(runner, "_run_bounded_process", fake_run)
    assert (
        _REAL_INSTALLED_OPENCODE_PLUGIN_BYTES(windows_executable)
        == b"candidate plugin bytes"
    )
    assert calls and calls[0][0] == windows_python
    assert calls[0][1:2] == (Path("-I"),)


def _owner_dotenv(path: Path) -> Path:
    path.write_bytes(b"synthetic owner dotenv metadata fixture")
    if os.name != "nt":
        path.chmod(0o600)
    return path


def test_owner_dotenv_metadata_validation_fails_closed(tmp_path: Path) -> None:
    repository = tmp_path / "repository"
    repository.mkdir()
    external = tmp_path / "external"
    external.mkdir()

    with pytest.raises(runner.QualificationError, match="required"):
        runner._validate_owner_dotenv(None, repository=repository)
    with pytest.raises(runner.QualificationError, match="absolute"):
        runner._validate_owner_dotenv(Path(".env"), repository=repository)
    with pytest.raises(runner.QualificationError, match="unavailable"):
        runner._validate_owner_dotenv(external / "missing.env", repository=repository)

    target = _owner_dotenv(external / "target.env")
    symlink = external / "symlink.env"
    symlink.symlink_to(target)
    with pytest.raises(runner.QualificationError, match="non-symlink"):
        runner._validate_owner_dotenv(symlink, repository=repository)

    real_parent = external / "real-parent"
    real_parent.mkdir()
    parent_target = _owner_dotenv(real_parent / "parent-target.env")
    parent_symlink = tmp_path / "parent-link"
    parent_symlink.symlink_to(real_parent, target_is_directory=True)
    with pytest.raises(runner.QualificationError, match="parent must not be a symlink"):
        runner._validate_owner_dotenv(
            parent_symlink / parent_target.name,
            repository=repository,
        )

    hardlink = external / "hardlink.env"
    try:
        hardlink.hardlink_to(target)
    except OSError as exc:
        pytest.skip(f"hardlink fixture unavailable: {exc}")
    with pytest.raises(runner.QualificationError, match="one link"):
        runner._validate_owner_dotenv(hardlink, repository=repository)

    if os.name != "nt":
        non_owner_only = _owner_dotenv(external / "group-readable.env")
        non_owner_only.chmod(0o640)
        with pytest.raises(runner.QualificationError, match="owner-only"):
            runner._validate_owner_dotenv(non_owner_only, repository=repository)

    inside_repository = _owner_dotenv(repository / ".env")
    with pytest.raises(runner.QualificationError, match="outside the repository"):
        runner._validate_owner_dotenv(inside_repository, repository=repository)

    oversized = _owner_dotenv(external / "oversized.env")
    oversized.write_bytes(b"x" * (runner.MAX_OWNER_DOTENV_BYTES + 1))
    if os.name != "nt":
        oversized.chmod(0o600)
    with pytest.raises(runner.QualificationError, match="size bound"):
        runner._validate_owner_dotenv(oversized, repository=repository)


def test_owner_dotenv_is_metadata_only_and_reaches_launcher_environment(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    repository = tmp_path / "repository"
    repository.mkdir()
    dotenv = _owner_dotenv(tmp_path / "external.env")

    original_read_bytes = Path.read_bytes
    original_open = Path.open

    def forbidden_read_bytes(self: Path) -> bytes:
        if self == dotenv:
            raise AssertionError("runner must not read the owner dotenv")
        return original_read_bytes(self)

    def forbidden_open(self: Path, *args: object, **kwargs: object):  # type: ignore[no-untyped-def]
        if self == dotenv:
            raise AssertionError("runner must not open the owner dotenv")
        return original_open(self, *args, **kwargs)

    monkeypatch.setattr(Path, "read_bytes", forbidden_read_bytes)
    monkeypatch.setattr(Path, "open", forbidden_open)
    monkeypatch.setattr(
        runner,
        "_sha256_file",
        lambda path: (_ for _ in ()).throw(
            AssertionError("runner must not hash the owner dotenv")
        )
        if path == dotenv
        else "a" * 64,
    )

    environment = runner.build_host_environment(
        root=tmp_path,
        opencode_binary=tmp_path / "bin" / "opencode",
        node_binary=tmp_path / "bin" / "node",
        owner_dotenv=dotenv,
        repository=repository,
    )
    assert environment[runner._OWNER_DOTENV_ENV_NAME] == str(dotenv.resolve())
    assert runner._OWNER_DOTENV_ENV_NAME not in runner._build_mcp_environment(
        tmp_path, node_binary=tmp_path / "bin" / "node"
    )

    captured: list[dict[str, str]] = []

    def fake_run(*_args: object, **kwargs: object) -> dict[str, object]:
        value = kwargs["environment"]
        assert isinstance(value, dict)
        captured.append(value)
        return {
            "stdout": _availability_events(),
            "stderr": b"",
            "returncode": 0,
            "elapsed_ms": 1,
            "timed_out": False,
            "output_overflow": False,
        }

    monkeypatch.setattr(runner, "_run_opencode_command", fake_run)
    result = runner._probe_model_availability(
        tmp_path / "owner-broker",
        environment=environment,
        cwd=tmp_path,
    )
    assert result["status"] == "available"
    assert captured
    assert captured[0][runner._OWNER_DOTENV_ENV_NAME] == str(dotenv.resolve())


@pytest.mark.parametrize("mode", ["qualification", "diagnostic"])
def test_public_execute_requires_owner_dotenv_for_every_mode(
    mode: str, tmp_path: Path
) -> None:
    with pytest.raises(runner.QualificationError, match="required"):
        runner.execute_qualification(
            candidate_wheel=tmp_path / "candidate.whl",
            deeplaw_executable=tmp_path / "deeplaw",
            output_dir=tmp_path / "output",
            opencode_binary=tmp_path / "opencode",
            host_launcher=tmp_path / "owner-broker",
            human_gold_path=None,
            owner_dotenv=None,
            mode=mode,
        )


def test_all_modes_fail_before_candidate_or_host_without_v2_correlation(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    entered_candidate = False

    def forbidden_prepare(_self: object) -> None:
        nonlocal entered_candidate
        entered_candidate = True
        raise AssertionError("candidate preparation must remain unreachable")

    monkeypatch.setattr(
        runner.QualificationOrchestrator,
        "prepare_candidate",
        forbidden_prepare,
    )
    for mode in ("qualification", "diagnostic"):
        with pytest.raises(
            runner.QualificationError,
            match=r"fork-route and child plugin-event correlation.*not_executed",
        ):
            runner._execute_qualification_body(
                candidate_wheel=tmp_path / "candidate.whl",
                deeplaw_executable=tmp_path / "deeplaw",
                output_dir=tmp_path / "output",
                opencode_binary=tmp_path / "opencode",
                host_launcher=tmp_path / "owner-broker",
                human_gold_path=None,
                root=tmp_path / "root",
                mode=mode,
            )
    assert entered_candidate is False


def _assert_owner_external_zero_model_response_binds_actual_fork_and_barrier() -> None:
    now = datetime.now(UTC).replace(microsecond=0)
    nonce = _digest("opencode-zero-model-nonce")
    request = _zero_model_request(nonce_sha256=nonce, now=now)
    response = _zero_model_response(nonce_sha256=nonce, now=now)

    admitted = runner.validate_opencode_zero_model_preflight_response(
        response,
        request=request,
        observed_at=now.strftime("%Y-%m-%dT%H:%M:%SZ"),
        seen_nonce_sha256s=set(),
    )

    proof = admitted["proof"]
    assert proof["request_body_sha256"] == hashlib.sha256(b"{}").hexdigest()
    assert proof["parent_session_sha256"] != proof["child_session_sha256"]
    assert proof["child_session_sha256"] == proof["child_plugin_session_sha256"]
    assert response["event_barrier"] == {
        "status": "satisfied",
        "response_release": "after_child_plugin_event",
        "timed_out": False,
        "child_plugin_event_count": 1,
        "event_type": "session.created",
        "timeout_seconds": 30,
        "elapsed_ms": 7,
        "parent_source": "actual_ingress_route",
    }


def _assert_owner_external_consumer_rejects_duplicate_json_and_never_self_signs(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    with pytest.raises(runner.QualificationError, match="duplicate"):
        runner._strict_control_json(b'{"status":"observed","status":"forged"}')

    now = datetime.now(UTC).replace(microsecond=0)
    nonce = _digest("opencode-no-self-sign")
    request = _zero_model_request(nonce_sha256=nonce, now=now)
    response = _zero_model_response(nonce_sha256=nonce, now=now)
    monkeypatch.setattr(
        host_process_receipt_v2,
        "build_receipt",
        lambda **_kwargs: (_ for _ in ()).throw(
            AssertionError("consumer must not create a v2 receipt")
        ),
    )
    assert runner.validate_opencode_zero_model_preflight_response(
        response,
        request=request,
        observed_at=now.strftime("%Y-%m-%dT%H:%M:%SZ"),
        seen_nonce_sha256s=set(),
    )["record_sha256"] == response["host_process_receipt"]["record_sha256"]


def _assert_owner_external_consumer_rejects_nonce_replay() -> None:
    now = datetime.now(UTC).replace(microsecond=0)
    nonce = _digest("opencode-replayed-zero-model-nonce")
    request = _zero_model_request(nonce_sha256=nonce, now=now)
    response = _zero_model_response(nonce_sha256=nonce, now=now)
    seen: set[str] = set()
    runner.validate_opencode_zero_model_preflight_response(
        response,
        request=request,
        observed_at=now.strftime("%Y-%m-%dT%H:%M:%SZ"),
        seen_nonce_sha256s=seen,
    )
    with pytest.raises(runner.QualificationError, match="v2 receipt"):
        runner.validate_opencode_zero_model_preflight_response(
            response,
            request=request,
            observed_at=now.strftime("%Y-%m-%dT%H:%M:%SZ"),
            seen_nonce_sha256s=seen,
        )


def _assert_owner_external_zero_model_response_rejects_forbidden_activity(
    field: str,
    value: int,
    message: str,
) -> None:
    now = datetime.now(UTC).replace(microsecond=0)
    nonce = _digest(f"opencode-zero-model-{field}")
    response = _zero_model_response(nonce_sha256=nonce, now=now)
    response[field] = value
    with pytest.raises(runner.QualificationError, match=message):
        runner.validate_opencode_zero_model_preflight_response(
            response,
            request=_zero_model_request(nonce_sha256=nonce, now=now),
            observed_at=now.strftime("%Y-%m-%dT%H:%M:%SZ"),
            seen_nonce_sha256s=set(),
        )


def _assert_owner_external_zero_model_response_rejects_unobserved_correlation(
    mutation: str,
) -> None:
    now = datetime.now(UTC).replace(microsecond=0)
    nonce = _digest(f"opencode-zero-model-{mutation}")
    request = _zero_model_request(nonce_sha256=nonce, now=now)
    response = _zero_model_response(nonce_sha256=nonce, now=now)
    if mutation == "barrier_timeout":
        response["event_barrier"]["timed_out"] = True
    elif mutation == "barrier_not_satisfied":
        response["event_barrier"]["status"] = "pending"
    elif mutation == "missing_child_event":
        response["event_barrier"]["child_plugin_event_count"] = 0
    elif mutation == "wrong_event_type":
        response["event_barrier"]["event_type"] = "session.updated"
    elif mutation == "response_released_early":
        response["event_barrier"]["response_release"] = "before_child_plugin_event"
    elif mutation == "unexpected_route":
        response["observed_sequence"].append("POST /session/:parent/message")
    else:
        receipt = response["host_process_receipt"]
        receipt["proof"]["request_body_sha256"] = hashlib.sha256(b'{"x":1}').hexdigest()
        receipt["proof"]["route_correlation_sha256"] = (
            host_process_receipt_v2.correlation_sha256(
                {
                    key: receipt["proof"][key]
                    for key in (
                        "process_identity_sha256",
                        "request_method",
                        "route_observation_sha256",
                        "request_body_sha256",
                        "response_sha256",
                        "parent_session_sha256",
                        "child_session_sha256",
                        "child_plugin_event_sha256",
                        "child_plugin_session_sha256",
                        "native_event_sequence_sha256",
                        "native_session_identity_sha256",
                        "native_lifecycle_record_sha256",
                    )
                }
            )
        )
        receipt["record_sha256"] = host_process_receipt_v2.record_sha256(receipt)
    with pytest.raises(runner.QualificationError):
        runner.validate_opencode_zero_model_preflight_response(
            response,
            request=request,
            observed_at=now.strftime("%Y-%m-%dT%H:%M:%SZ"),
            seen_nonce_sha256s=set(),
        )


def _assert_formal_runner_crosses_fail_before_only_after_external_preflight(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    order: list[str] = []

    def observed_preflight(**kwargs: object) -> dict[str, object]:
        order.append("owner_external_preflight")
        assert kwargs["task_case"] == "continuity"
        assert kwargs["run_id"] == "opencode-continuity-202"
        return {
            "status": "passed",
            "evidence_class": "zero_model_preflight_only",
            "formal_admission": False,
        }

    def reached_candidate(_self: object) -> None:
        order.append("candidate_preparation")
        raise RuntimeError("candidate sentinel")

    monkeypatch.setattr(
        runner,
        "run_opencode_owner_external_zero_model_preflight",
        observed_preflight,
    )
    monkeypatch.setattr(
        runner.QualificationOrchestrator,
        "prepare_candidate",
        reached_candidate,
    )
    with pytest.raises(RuntimeError, match="candidate sentinel"):
        owner_dotenv = _owner_dotenv(tmp_path / "owner.env")
        runner._execute_qualification_body(
            candidate_wheel=tmp_path / "candidate.whl",
            deeplaw_executable=tmp_path / "deeplaw",
            output_dir=tmp_path / "output",
            opencode_binary=tmp_path / "opencode",
            opencode_package=tmp_path / "opencode-package",
            host_launcher=tmp_path / "owner-broker",
            human_gold_path=None,
            owner_dotenv=owner_dotenv,
            host_identity_input=tmp_path / "host-identity.json",
            candidate_binding_input=tmp_path / "frozen-active-qualification.json",
            expected_broker_sha256="8" * 64,
            run_id="opencode-continuity-202",
            evidence_run_id=202,
            qualification_run_id=303,
            root=tmp_path / "root",
            mode="qualification",
        )
    assert order == ["owner_external_preflight", "candidate_preparation"]


def _assert_zero_model_preflight_is_static_until_external_broker_control(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    now = datetime.now(UTC).replace(microsecond=0)
    nonce = _digest("opencode-static-preflight-nonce")
    receipt = _zero_model_receipt(nonce_sha256=nonce, now=now)
    opencode_target = tmp_path / "opencode-target"
    opencode_target.write_bytes(b"static opencode target")
    opencode_target.chmod(0o700)
    opencode_selector = tmp_path / "opencode"
    opencode_selector.symlink_to(opencode_target.name)
    opencode_package = tmp_path / "opencode-package"
    opencode_package.write_bytes(b"pinned opencode 1.18.16 package")
    identity = {
        "schema_version": "deeplaw.host-exact-identity/v1",
        "hosts": {
            "opencode": {
                **_ZERO_MODEL_HOST_ITEM,
                "executable_sha256": hashlib.sha256(
                    opencode_target.read_bytes()
                ).hexdigest(),
                "package_sha256": hashlib.sha256(
                    opencode_package.read_bytes()
                ).hexdigest(),
            }
        },
        "source_sha256": "9" * 64,
        "source_bytes": 512,
    }
    calls: list[str] = []
    loaded_candidate_inputs: list[Path] = []

    def load_candidate(path: Path, **kwargs: object) -> dict[str, Any]:
        del kwargs
        loaded_candidate_inputs.append(path)
        return dict(_ZERO_MODEL_CANDIDATE)

    monkeypatch.setattr(
        runner,
        "load_zero_model_candidate_binding",
        load_candidate,
    )
    monkeypatch.setattr(
        runner.host_preflight_receipt,
        "load_host_identity_input",
        lambda *args, **kwargs: identity,
    )
    monkeypatch.setattr(
        runner.subprocess,
        "run",
        lambda *args, **kwargs: (_ for _ in ()).throw(
            AssertionError("static preflight must not execute any Host command")
        ),
    )

    @contextmanager
    def staged(*args, **kwargs):
        del args, kwargs
        calls.append("stage_exact_broker")
        yield tmp_path / "staged-broker"

    def consumed(_broker: Path, **kwargs: object) -> dict[str, Any]:
        calls.append("consume_external_broker")
        request = kwargs["request"]
        response = _zero_model_response(
            nonce_sha256=request["challenge"]["nonce_sha256"],
            now=datetime.now(UTC).replace(microsecond=0),
        )
        response["host_process_receipt"] = receipt
        response["host_process_receipt"]["nonce_sha256"] = request["challenge"][
            "nonce_sha256"
        ]
        response["host_process_receipt"]["record_sha256"] = (
            host_process_receipt_v2.record_sha256(response["host_process_receipt"])
        )
        return response["host_process_receipt"]

    monkeypatch.setattr(runner, "_stage_exact_broker_executable", staged)
    monkeypatch.setattr(runner, "consume_opencode_zero_model_preflight", consumed)
    monkeypatch.setattr(
        runner,
        "_run_opencode_command",
        lambda *args, **kwargs: (_ for _ in ()).throw(
            AssertionError("static preflight must not execute OpenCode")
        ),
    )
    result = runner.run_opencode_owner_external_zero_model_preflight(
        candidate_binding_input=tmp_path / "construction-kit-manifest.json",
        host_identity_input=tmp_path / "host-identity.json",
        opencode_package=opencode_package,
        opencode_binary=opencode_selector,
        opencode_broker=tmp_path / "owner-broker",
        expected_broker_sha256="8" * 64,
        task_case="continuity",
        run_id="opencode-zero-model-preflight-202-303",
        evidence_run_id=202,
        qualification_run_id=303,
        repository=_REPOSITORY,
    )
    assert calls == ["stage_exact_broker", "consume_external_broker"]
    assert loaded_candidate_inputs == [tmp_path / "construction-kit-manifest.json"]
    assert result == {
        "status": "passed",
        "evidence_class": "zero_model_preflight_only",
        "formal_admission": False,
        "host": "opencode",
        "observed_sequence": list(runner.OPENCODE_ZERO_MODEL_REQUIRED_SEQUENCE),
        "model_invocation_count": 0,
        "provider_request_count": 0,
        "broker_source_sha256": "8" * 64,
        "receipt_record_sha256": receipt["record_sha256"],
    }


def _assert_zero_model_static_binary_topology_and_hash_fail_closed(
    topology: str,
    message: str,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    repository = tmp_path / "repository"
    repository.mkdir()
    external = tmp_path / "external"
    external.mkdir()
    target_parent = repository if topology == "repository_internal" else external
    target = target_parent / "opencode-target"
    target_bytes = b"exact static opencode target"
    target.write_bytes(target_bytes)
    target.chmod(0o700)
    selector = target_parent / "opencode"
    selector.symlink_to(target.name)
    selected = selector

    if topology == "selector_chain":
        intermediate = target_parent / "opencode-intermediate"
        intermediate.symlink_to(target.name)
        selector.unlink()
        selector.symlink_to(intermediate.name)
    elif topology == "parent_symlink":
        linked_parent = tmp_path / "linked-external"
        linked_parent.symlink_to(external.name)
        selected = linked_parent / "opencode"
    elif topology == "target_hardlink":
        os.link(target, target_parent / "opencode-hardlink")
    elif topology == "target_not_executable":
        target.chmod(0o600)
    elif topology == "target_not_regular":
        target.unlink()
        target.mkdir()

    expected_sha256 = hashlib.sha256(target_bytes).hexdigest()
    if topology == "hash_drift":
        expected_sha256 = "6" * 64
        real_fstat = runner.os.fstat

        def windows_normalized_fstat(fd: int) -> SimpleNamespace:
            details = real_fstat(fd)
            return SimpleNamespace(
                st_dev=details.st_dev,
                st_ino=details.st_ino,
                st_size=details.st_size,
                st_mode=stat.S_IFREG | 0o600,
                st_uid=0,
                st_nlink=details.st_nlink,
                st_mtime=details.st_mtime + 1,
                st_mtime_ns=details.st_mtime_ns + 1_000_000_000,
                st_ctime=details.st_ctime + 1,
                st_ctime_ns=details.st_ctime_ns + 1_000_000_000,
            )

        monkeypatch.setattr(runner.os, "fstat", windows_normalized_fstat)
    identity = {
        "hosts": {
            "opencode": {
                **_ZERO_MODEL_HOST_ITEM,
                "executable_sha256": expected_sha256,
            }
        },
        "source_sha256": "9" * 64,
    }
    monkeypatch.setattr(
        runner.subprocess,
        "run",
        lambda *args, **kwargs: (_ for _ in ()).throw(
            AssertionError("static binary inspection must not execute a Host command")
        ),
    )
    with pytest.raises(runner.QualificationError, match=message):
        runner._inspect_opencode_binary_static(
            selected,
            identity=identity,
            repository=repository,
        )


def _assert_zero_model_static_boundary_reaches_only_external_broker_popen(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    opencode_target = tmp_path / "opencode-target"
    opencode_target.write_bytes(b"static opencode target")
    opencode_target.chmod(0o700)
    opencode_selector = tmp_path / "opencode"
    opencode_selector.symlink_to(opencode_target.name)
    opencode_package = tmp_path / "opencode-package"
    opencode_package.write_bytes(b"pinned opencode package")
    broker = tmp_path / "owner-broker"
    broker.write_bytes(b"#!/bin/sh\nexit 99\n")
    broker.chmod(0o700)
    broker_sha256 = hashlib.sha256(broker.read_bytes()).hexdigest()
    identity = {
        "schema_version": "deeplaw.host-exact-identity/v1",
        "hosts": {
            "opencode": {
                **_ZERO_MODEL_HOST_ITEM,
                "executable_sha256": hashlib.sha256(
                    opencode_target.read_bytes()
                ).hexdigest(),
                "package_sha256": hashlib.sha256(
                    opencode_package.read_bytes()
                ).hexdigest(),
            }
        },
        "source_sha256": "9" * 64,
        "source_bytes": 512,
    }
    monkeypatch.setattr(
        runner,
        "load_zero_model_candidate_binding",
        lambda *args, **kwargs: dict(_ZERO_MODEL_CANDIDATE),
    )
    monkeypatch.setattr(
        runner.host_preflight_receipt,
        "load_host_identity_input",
        lambda *args, **kwargs: identity,
    )
    monkeypatch.setattr(
        runner.subprocess,
        "run",
        lambda *args, **kwargs: (_ for _ in ()).throw(
            AssertionError("runner must not execute an OpenCode Host command")
        ),
    )
    popen_calls: list[tuple[list[str], dict[str, object]]] = []

    class BrokerControlReached(Exception):
        pass

    def broker_popen(argv: list[str], **kwargs: object) -> None:
        popen_calls.append((argv, kwargs))
        assert argv[0] != str(opencode_selector)
        assert argv[0] != str(opencode_target)
        assert Path(argv[0]).name == "broker-executable"
        assert argv[1:] == [runner.OPENCODE_BROKER_CONTROL_ARGUMENT]
        raise BrokerControlReached

    arguments = {
        "candidate_binding_input": tmp_path / "frozen-active-qualification.json",
        "host_identity_input": tmp_path / "host-identity.json",
        "opencode_package": opencode_package,
        "opencode_binary": opencode_selector,
        "opencode_broker": broker,
        "expected_broker_sha256": broker_sha256,
        "task_case": "continuity",
        "run_id": "opencode-zero-model-preflight-202-303",
        "evidence_run_id": 202,
        "qualification_run_id": 303,
        "repository": _REPOSITORY,
    }
    if os.name == "nt":
        # Native ACL hardening uses bounded PowerShell children. Exercise that
        # production path before intercepting only the final broker exchange.
        def broker_exchange(
            broker_executable: Path,
            *,
            payload: bytes,
            timeout_seconds: float,
        ) -> bytes:
            assert payload
            popen_calls.append(
                (
                    [
                        str(broker_executable),
                        runner.OPENCODE_BROKER_CONTROL_ARGUMENT,
                    ],
                    {"timeout_seconds": timeout_seconds},
                )
            )
            assert broker_executable != opencode_selector
            assert broker_executable != opencode_target
            assert broker_executable.name == "broker-executable"
            raise BrokerControlReached

        monkeypatch.setattr(runner, "_bounded_broker_control_exchange", broker_exchange)
        with pytest.raises(BrokerControlReached):
            runner.run_opencode_owner_external_zero_model_preflight(**arguments)
    else:
        monkeypatch.setattr(runner.subprocess, "Popen", broker_popen)
        with pytest.raises(BrokerControlReached):
            runner.run_opencode_owner_external_zero_model_preflight(**arguments)
    assert len(popen_calls) == 1


def _assert_zero_model_static_package_binding_rejects_unpinned_release(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    package = tmp_path / "opencode-package"
    package.write_bytes(b"pinned opencode package bytes")
    package_sha256 = hashlib.sha256(package.read_bytes()).hexdigest()
    identity = {
        "hosts": {
            "opencode": {
                **_ZERO_MODEL_HOST_ITEM,
                "package_sha256": package_sha256,
            }
        }
    }
    binding_messages: list[str] = []
    stable_binding = runner._validate_stable_path_fd_binding

    def observed_binding(**kwargs: object) -> None:
        binding_messages.append(str(kwargs["error_message"]))
        stable_binding(**kwargs)

    monkeypatch.setattr(runner, "_validate_stable_path_fd_binding", observed_binding)
    assert runner._validate_opencode_package(
        package,
        identity=identity,
        repository=_REPOSITORY,
    ) == {
        "version": "1.18.16",
        "source_commit": runner.OPENCODE_SOURCE_COMMIT,
        "package_sha256": package_sha256,
    }
    assert binding_messages == ["OpenCode package source changed while it was read"]

    identity["hosts"]["opencode"]["source_commit"] = "a" * 40
    with pytest.raises(runner.QualificationError, match="pinned release"):
        runner._validate_opencode_package(
            package,
            identity=identity,
            repository=_REPOSITORY,
        )

    identity["hosts"]["opencode"]["source_commit"] = runner.OPENCODE_SOURCE_COMMIT
    package.write_bytes(b"different package bytes")
    with pytest.raises(runner.QualificationError, match="bytes differ"):
        runner._validate_opencode_package(
            package,
            identity=identity,
            repository=_REPOSITORY,
        )


def _assert_zero_model_cli_requires_no_dotenv_or_formal_output(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    observed: dict[str, object] = {}

    def preflight(**kwargs: object) -> dict[str, object]:
        observed.update(kwargs)
        return {"status": "passed"}

    monkeypatch.setattr(
        runner,
        "run_opencode_owner_external_zero_model_preflight",
        preflight,
    )
    assert runner.main(
        [
            "--zero-model-preflight",
            "--candidate-binding-input",
            str(tmp_path / "frozen-active-qualification.json"),
            "--host-identity-input",
            str(tmp_path / "host-identity.json"),
            "--opencode-package",
            str(tmp_path / "opencode-package"),
            "--opencode-binary",
            str(tmp_path / "opencode"),
            "--opencode-launcher",
            str(tmp_path / "owner-broker"),
            "--expected-broker-sha256",
            "8" * 64,
            "--task-case",
            "continuity",
            "--run-id",
            "opencode-zero-model-preflight-202-303",
            "--evidence-run-id",
            "202",
            "--qualification-run-id",
            "303",
        ]
    ) == 0
    assert observed["candidate_wheel"] is None
    assert observed["opencode_package"] == tmp_path / "opencode-package"
    assert "owner_dotenv" not in observed
    assert "output_dir" not in observed


def test_owner_dotenv_path_is_not_retained_in_public_forbidden_output() -> None:
    dotenv = "/external/owner-only/.env"
    with pytest.raises(runner.QualificationError):
        runner._forbid_sensitive(
            json.dumps({"error": dotenv}).encode("utf-8"),
            forbidden_values=(dotenv,),
        )


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


def test_windows_process_group_options_require_bound_taskkill(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    system_root = tmp_path / "Windows"
    system_root.mkdir()

    with monkeypatch.context() as windows:
        windows.setattr(runner.os, "name", "nt")
        windows.setattr(
            runner.subprocess,
            "CREATE_NEW_PROCESS_GROUP",
            512,
            raising=False,
        )
        windows.setenv("SYSTEMROOT", str(system_root))
        assert runner.process_creation_options() == {"creationflags": 512}

        windows.delenv("SYSTEMROOT", raising=False)
        windows.delenv("WINDIR", raising=False)
        assert runner.process_creation_options() == {"creationflags": 512}
        with pytest.raises(
            runner.QualificationError,
            match="Windows system root is unavailable",
        ):
            runner._windows_child_environment({"PATH": os.defpath})


@pytest.mark.parametrize("failure", ("nonzero", "exception", "timeout"))
def test_windows_process_tree_cleanup_reports_unconfirmed_taskkill(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    failure: str,
) -> None:
    del tmp_path

    class Process:
        pid = 4315

        def __init__(self) -> None:
            self.killed = False

        def poll(self) -> None:
            return None

        def kill(self) -> None:
            self.killed = True

    cleanup_timeouts: list[float] = []

    class Guard:
        def cleanup(self, *, timeout_seconds: float) -> bool:
            cleanup_timeouts.append(timeout_seconds)
            if failure == "exception":
                raise OSError("synthetic job cleanup failure")
            if failure == "timeout":
                raise subprocess.TimeoutExpired("synthetic job cleanup", timeout_seconds)
            return False

    with monkeypatch.context() as windows:
        windows.setattr(runner.os, "name", "nt")
        process = Process()
        confirmed = runner._terminate_process_tree(  # type: ignore[arg-type]
            process,
            Guard(),  # type: ignore[arg-type]
        )

    assert confirmed is False
    assert process.killed is True
    assert cleanup_timeouts == [5]

    if failure == "nonzero":
        with monkeypatch.context() as windows:
            windows.setattr(runner.os, "name", "nt")
            missing_guard = Process()
            assert runner._terminate_process_tree(  # type: ignore[arg-type]
                missing_guard
            ) is False
        assert missing_guard.killed is True


def test_bounded_process_timeout_does_not_use_unbounded_communicate(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    class FakeProcess:
        pid = 4316
        returncode = -9

        def __init__(self) -> None:
            self.timeouts: list[float | None] = []

        def communicate(
            self, *, input: bytes = b"", timeout: float | None = None
        ) -> tuple[bytes, bytes]:
            del input
            self.timeouts.append(timeout)
            if timeout is not None and len(self.timeouts) == 1:
                raise runner.subprocess.TimeoutExpired("fake", timeout)
            if timeout is None:
                raise AssertionError("unbounded communicate")
            return b"", b""

        def poll(self) -> None:
            return None

    fake = FakeProcess()
    guard = object()
    terminated: list[tuple[object, object | None]] = []
    monkeypatch.setattr(
        runner.bounded_subprocess,
        "spawn_process",
        lambda *args, **kwargs: (fake, guard),
    )
    monkeypatch.setattr(
        runner,
        "_terminate_process_tree",
        lambda process, selected_guard=None: terminated.append(
            (process, selected_guard)
        )
        or True,
    )
    result = runner._run_bounded_process(
        ["fake-opencode"],
        environment={"PATH": os.defpath},
        cwd=tmp_path,
        timeout=0.01,
    )
    assert result["timed_out"] is True
    assert fake.timeouts[0] == 0.01
    assert fake.timeouts[1] is not None
    assert terminated == [(fake, guard)]


def test_local_server_stop_does_not_use_unbounded_communicate(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    class FakeProcess:
        pid = 4317

        def __init__(self) -> None:
            self.timeouts: list[float | None] = []
            self.killed = False

        def poll(self) -> None:
            return None

        def communicate(
            self, *, timeout: float | None = None
        ) -> tuple[bytes, bytes]:
            self.timeouts.append(timeout)
            if len(self.timeouts) == 1:
                raise runner.subprocess.TimeoutExpired("fake", timeout)
            if timeout is None:
                raise AssertionError("unbounded communicate")
            return b"", b""

        def kill(self) -> None:
            self.killed = True

    fake = FakeProcess()
    server = runner._OpenCodeLocalServer(
        binary=tmp_path / "opencode",
        environment={},
        cwd=tmp_path,
        root=tmp_path,
    )
    server.process = fake  # type: ignore[assignment]
    monkeypatch.setattr(
        runner,
        "_terminate_process_tree",
        lambda _process, _guard=None: True,
    )
    server.stop()
    assert fake.killed is True
    assert fake.timeouts[0] == 10
    assert fake.timeouts[1] is not None
    assert server.process is None


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


def _assert_owner_broker_executes_from_exact_private_staged_bytes(tmp_path: Path) -> None:
    assert runner._windows_acl_hardening_verified(
        {
            "platform": "nt",
            "applied": True,
            "verification": {"permissions_verified": True},
        }
    )
    for invalid_report in (
        None,
        {"platform": "nt", "applied": False},
        {
            "platform": "nt",
            "applied": True,
            "verification": {"permissions_verified": False},
        },
    ):
        assert not runner._windows_acl_hardening_verified(invalid_report)

    def metadata(**updates: int) -> SimpleNamespace:
        values = {
            "st_dev": 7,
            "st_ino": 11,
            "st_size": 19,
            "st_mode": stat.S_IFREG | 0o700,
            "st_uid": 501,
            "st_nlink": 1,
            "st_mtime": 100,
            "st_mtime_ns": 100,
            "st_ctime": 200,
            "st_ctime_ns": 200,
        }
        values.update(updates)
        return SimpleNamespace(**values)

    path_snapshot = metadata()
    windows_fd_snapshot = metadata(
        st_mode=stat.S_IFREG | 0o600,
        st_uid=0,
        st_mtime=101,
        st_mtime_ns=101,
        st_ctime=201,
        st_ctime_ns=201,
    )
    runner._validate_stable_path_fd_binding(
        path_before=path_snapshot,
        path_after=path_snapshot,
        fd_before=windows_fd_snapshot,
        fd_after=windows_fd_snapshot,
        observed_bytes=19,
        error_message="OpenCode owner-external broker source changed while it was read",
    )
    for changed in (
        {
            "path_after": metadata(st_mtime=999, st_mtime_ns=999),
            "fd_after": windows_fd_snapshot,
        },
        {
            "path_after": path_snapshot,
            "fd_after": metadata(
                st_mode=stat.S_IFREG | 0o600,
                st_uid=0,
                st_mtime=999,
                st_mtime_ns=999,
                st_ctime=201,
                st_ctime_ns=201,
            ),
        },
        {
            "path_after": path_snapshot,
            "fd_after": metadata(
                st_ino=12,
                st_mode=stat.S_IFREG | 0o600,
                st_uid=0,
                st_mtime=101,
                st_mtime_ns=101,
                st_ctime=201,
                st_ctime_ns=201,
            ),
            "fd_before": metadata(
                st_ino=12,
                st_mode=stat.S_IFREG | 0o600,
                st_uid=0,
                st_mtime=101,
                st_mtime_ns=101,
                st_ctime=201,
                st_ctime_ns=201,
            ),
        },
        {
            "path_before": metadata(st_ino=0),
            "path_after": metadata(st_ino=0),
            "fd_before": metadata(
                st_ino=0,
                st_mode=stat.S_IFREG | 0o600,
                st_uid=0,
                st_mtime=101,
                st_mtime_ns=101,
                st_ctime=201,
                st_ctime_ns=201,
            ),
            "fd_after": metadata(
                st_ino=0,
                st_mode=stat.S_IFREG | 0o600,
                st_uid=0,
                st_mtime=101,
                st_mtime_ns=101,
                st_ctime=201,
                st_ctime_ns=201,
            ),
        },
    ):
        with pytest.raises(runner.QualificationError, match="changed while it was read"):
            runner._validate_stable_path_fd_binding(
                path_before=changed.get("path_before", path_snapshot),
                path_after=changed["path_after"],
                fd_before=changed.get("fd_before", windows_fd_snapshot),
                fd_after=changed["fd_after"],
                observed_bytes=19,
                error_message=(
                    "OpenCode owner-external broker source changed while it was read"
                ),
            )

    host = tmp_path / "opencode"
    host.write_bytes(b"host-binary")
    broker = tmp_path / "owner-broker"
    broker.write_bytes(b"#!/bin/sh\nexit 0\n")
    broker.chmod(0o700)
    expected = hashlib.sha256(broker.read_bytes()).hexdigest()

    with runner._stage_exact_broker_executable(
        broker,
        repository=_REPOSITORY,
        host_binary=host,
        expected_sha256=expected,
    ) as staged:
        assert staged != broker
        assert staged.read_bytes() == broker.read_bytes()
        broker.write_bytes(b"#!/bin/sh\nexit 91\n")
        assert hashlib.sha256(staged.read_bytes()).hexdigest() == expected
        if os.name == "nt":
            from deeplaw.windows_acl import native_windows_path_acl_report

            assert native_windows_path_acl_report(staged.parent)[
                "permissions_verified"
            ]
            assert native_windows_path_acl_report(staged)["permissions_verified"]
        else:
            assert stat.S_IMODE(staged.parent.stat().st_mode) == 0o500
            assert staged.parent.stat().st_uid == os.geteuid()
            assert stat.S_IMODE(staged.stat().st_mode) == 0o500
    assert not staged.exists()


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
            if timeout is not None and timeout <= 0.01:
                raise runner.subprocess.TimeoutExpired("fake", timeout)
            return b"", b""

        def poll(self) -> None:
            return None

    fake = FakeProcess()
    guard = object()
    killed: list[tuple[object, object | None]] = []
    monkeypatch.setattr(
        runner.bounded_subprocess,
        "spawn_process",
        lambda *args, **kwargs: (fake, guard),
    )
    monkeypatch.setattr(
        runner,
        "_terminate_process_tree",
        lambda process, selected_guard=None: killed.append(
            (process, selected_guard)
        )
        or True,
    )
    result = runner._run_bounded_process(
        ["fake-opencode"], environment={"PATH": os.defpath}, cwd=tmp_path, timeout=0.01
    )
    assert result["timed_out"] is True
    assert killed == [(fake, guard)]


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


def _assert_owner_external_regression_bundle(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    tmp_path.mkdir()
    _assert_owner_external_zero_model_response_binds_actual_fork_and_barrier()
    with monkeypatch.context() as scoped:
        _assert_owner_external_consumer_rejects_duplicate_json_and_never_self_signs(
            scoped
        )
    _assert_owner_external_consumer_rejects_nonce_replay()
    for field in (
        "forbidden_route_count",
        "message_route_count",
        "provider_route_count",
        "model_route_count",
        "mcp_route_count",
        "model_invocation_count",
        "provider_request_count",
        "remote_workspace_forward_count",
        "share_request_count",
        "ambient_plugin_count",
    ):
        _assert_owner_external_zero_model_response_rejects_forbidden_activity(
            field,
            1,
            "forbidden",
        )
    for mutation in (
        "barrier_timeout",
        "barrier_not_satisfied",
        "missing_child_event",
        "wrong_event_type",
        "response_released_early",
        "unexpected_route",
        "nonempty_fork_body",
    ):
        _assert_owner_external_zero_model_response_rejects_unobserved_correlation(
            mutation
        )

    def case_root(name: str) -> Path:
        selected = tmp_path / name
        selected.mkdir()
        return selected

    with monkeypatch.context() as scoped:
        _assert_formal_runner_crosses_fail_before_only_after_external_preflight(
            scoped,
            case_root("formal-runner"),
        )
    with monkeypatch.context() as scoped:
        _assert_zero_model_preflight_is_static_until_external_broker_control(
            scoped,
            case_root("static-preflight"),
        )
    topology_cases = [
        ("hash_drift", "hash differs"),
        ("selector_chain", "symlink chain"),
        ("parent_symlink", "parent path"),
        ("target_hardlink", "single-link"),
        ("target_not_regular", "regular single-link"),
        ("repository_internal", "repository-external"),
    ]
    if os.name != "nt":
        topology_cases.insert(-1, ("target_not_executable", "not executable"))
    for index, (topology, message) in enumerate(topology_cases):
        with monkeypatch.context() as scoped:
            _assert_zero_model_static_binary_topology_and_hash_fail_closed(
                topology,
                message,
                scoped,
                case_root(f"topology-{index}"),
            )
    with monkeypatch.context() as scoped:
        _assert_zero_model_static_boundary_reaches_only_external_broker_popen(
            scoped,
            case_root("broker-boundary"),
        )
    with monkeypatch.context() as scoped:
        _assert_zero_model_static_package_binding_rejects_unpinned_release(
            scoped,
            case_root("package-binding"),
        )
    with monkeypatch.context() as scoped:
        _assert_zero_model_cli_requires_no_dotenv_or_formal_output(
            scoped,
            case_root("zero-model-cli"),
        )
    _assert_owner_broker_executes_from_exact_private_staged_bytes(
        case_root("staged-broker")
    )


def test_preflight_keeps_owner_broker_out_of_static_inspection_commands(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Preserve the frozen node ID; Host commands now route only through the broker.
    _assert_owner_external_regression_bundle(
        monkeypatch,
        tmp_path / "owner-external-regressions",
    )
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
        assert binary == tmp_path / "owner-broker"
        del cwd
        calls.append((args, environment))
        if args == ("models", "deepseek"):
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
        assert binary == tmp_path / "owner-broker"
        del environment, cwd
        outputs = {
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
        assert binary == tmp_path / "owner-broker"
        del environment, cwd
        outputs = {
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
        owner_dotenv=_owner_dotenv(tmp_path / "owner.env"),
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
            owner_dotenv=_owner_dotenv(tmp_path / "owner.env"),
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
            "--opencode-package",
            str(tmp_path / "opencode-package"),
            "--opencode-launcher",
            str(tmp_path / "owner-broker"),
            "--opencode-dotenv",
            str(tmp_path / "owner.env"),
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
