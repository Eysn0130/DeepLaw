"""Synthetic protocol tests. These never launch OpenCode or a Provider."""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta

import pytest

from benchmarks.hosts import opencode_single_task_producer as producer


def _control():
    now = datetime.now(UTC)
    return {
        "schema_version": producer.CONTROL,
        "run_id": "synthetic-continuity",
        "workflow_run_id": 1,
        "host": "opencode",
        "task_case": "continuity",
        "candidate_binding": {
            "commit": "a" * 40,
            "tree": "b" * 40,
            "lock_sha256": "c" * 64,
            "wheel_sha256": "d" * 64,
            "sdist_sha256": "e" * 64,
        },
        "host_identity_sha256": "1" * 64,
        "host_identity_source_sha256": "2" * 64,
        "broker_source_sha256": "3" * 64,
        "nonce_sha256": "4" * 64,
        "issued_at": now.isoformat(),
        "expires_at": (now + timedelta(minutes=10)).isoformat(),
        "max_turns": 2,
        "max_calls_per_turn": 2,
        "max_read_chars": 4000,
        "deployment_source_closure_sha256": "5" * 64,
        "mcp_launch_prefix_sha256": "6" * 64,
        "original_plugin_sha256": "7" * 64,
        "privacy_wrapper_sha256": "8" * 64,
        "max_guard_requests": 6,
        "formal_admission": False,
        "credential_delivery_mode": "owner_external_guard_host_nonce_mcp_no_key",
        "formal_gaps": [
            "os_isolation_unobserved",
            "global_network_and_cost_unobserved",
            "six_slot_formal_qualification_not_executed",
        ],
    }


def test_control_rejects_expiry_unknown_fields_and_widened_budget():
    value = _control()
    producer.validate_control(value)
    for change in ({"max_turns": 3}, {"model_invocation_count": 0}, {"formal_admission": True}):
        with pytest.raises(__import__("jsonschema").ValidationError):
            producer.validate_control({**value, **change})
    with pytest.raises(producer.ProducerError, match="expired"):
        producer.validate_control(value, now=datetime.now(UTC) + timedelta(hours=1))


def test_live_tool_definition_is_v8_and_old_schema_remains_rejected():
    from deeplaw.knowledge_mcp_server import _v7_input_schema, knowledge_tool_definition

    tool = knowledge_tool_definition(autonomous=True).model_dump(by_alias=True, exclude_none=True)
    measured = producer.advertised_receipt([tool])
    assert measured["tool_definition_bytes"] <= 12288
    with pytest.raises(producer.ProducerError):
        producer.advertised_receipt([{**tool, "inputSchema": _v7_input_schema()}])


@pytest.mark.parametrize("mutation", ["scope", "sensitivity", "target", "offset", "third"])
def test_budget_rejects_scope_revision_and_extra_calls(mutation):
    target = {
        "kind": "knowledge",
        "knowledge_id": "knowledge_" + "a" * 24,
        "revision_id": "knowledgerev_" + "b" * 24,
    }
    budget = producer.ReadBudget()
    budget.request(
        {
            "operation": "query",
            "query": "public procedure",
            "scope": "project",
            "max_sensitivity": "public",
            "max_chars": 4000,
        }
    )
    budget.targets = [target]
    request = {
        "operation": "read",
        "target": target,
        "scope": "project",
        "max_sensitivity": "public",
        "max_chars": 4000,
    }
    if mutation == "scope":
        request["scope"] = "personal"
    elif mutation == "sensitivity":
        request["max_sensitivity"] = "private"
    elif mutation == "target":
        request["target"] = {**target, "revision_id": "knowledgerev_" + "c" * 24}
    elif mutation == "offset":
        request.update(offset=1, content_sha256="a" * 64)
    else:
        budget.request(request)
    with pytest.raises(producer.ProducerError):
        budget.request(request)


def _guard_body(content="Use only the exact governed context."):
    return json.dumps(
        {
            "model": "deepseek-v4-flash",
            "messages": [
                {"role": "system", "content": content},
                {"role": "user", "content": "Continue."},
            ],
            "stream": True,
        }
    ).encode()


def test_guard_requires_active_exact_route_and_does_not_infer_global_zero():
    guard = producer.RequestGuard(key="private-test-key", nonce="synthetic-nonce")
    with pytest.raises(producer.ProducerError):
        guard.inspect(
            _guard_body(), path="/chat/completions", authorization="Bearer synthetic-nonce"
        )
    guard.active = True
    assert (
        guard.inspect(
            _guard_body(), path="/chat/completions", authorization="Bearer synthetic-nonce"
        )["model"]
        == "deepseek-v4-flash"
    )
    for path, auth in [
        ("/models", "Bearer synthetic-nonce"),
        ("/chat/completions", "Bearer wrong"),
    ]:
        with pytest.raises(producer.ProducerError):
            guard.inspect(_guard_body(), path=path, authorization=auth)
    assert guard.requests == []  # inspect alone is not a forwarded network observation.


@pytest.mark.parametrize(
    "content", ["Working directory: /Users/synthetic/project", "private-test-key"]
)
def test_guard_rejects_actual_outbound_paths_and_canary(content):
    guard = producer.RequestGuard(key="private-test-key", nonce="synthetic-nonce")
    guard.active = True
    with pytest.raises((ValueError, PermissionError)):
        guard.inspect(
            _guard_body(content), path="/chat/completions", authorization="Bearer synthetic-nonce"
        )


def test_guard_rejects_unknown_structure_and_oversize():
    guard = producer.RequestGuard(key="private-test-key", nonce="synthetic-nonce")
    guard.active = True
    for raw in (
        producer.encoded({"model": "deepseek-v4-flash", "messages": [], "unknown": True}),
        b"x" * 262145,
    ):
        with pytest.raises(producer.ProducerError):
            guard.inspect(raw, path="/chat/completions", authorization="Bearer synthetic-nonce")


@pytest.mark.parametrize("mutation", [None, "session", "call_id", "input", "output", "status"])
def test_native_tool_part_is_bound_to_observed_mcp_bytes(mutation):
    parts, records = [], []
    for index, operation in enumerate(("context", "read")):
        args, text = {"operation": operation}, "bounded-" + operation
        parts.append(
            {
                "type": "tool",
                "sessionID": "session-one",
                "messageID": "message-one",
                "callID": str(index),
                "tool": producer.TOOL,
                "state": {"status": "completed", "input": args, "output": text},
            }
        )
        records.append(
            {
                "arguments_sha256": producer.digest(args),
                "content_sha256": producer.digest(text.encode()),
                "content_bytes": len(text),
            }
        )
    if mutation == "session":
        parts[1]["sessionID"] = "another-session"
    elif mutation == "call_id":
        parts[1]["callID"] = "0"
    elif mutation in {"input", "output", "status"}:
        parts[1]["state"][mutation] = "tampered"
    if mutation is None:
        assert len(producer.correlate_calls(parts, records, session="session-one")) == 2
    else:
        with pytest.raises(producer.ProducerError):
            producer.correlate_calls(parts, records, session="session-one")


@pytest.mark.parametrize("wrong_response_target", [False, True])
def test_synthetic_two_turn_sources_reopen_without_formal_promotion(
    tmp_path, wrong_response_target,
):
    from deeplaw.native_host import derive_native_host_receipt
    from tests.test_v013_host_task_evidence import _event, _host_identity

    control = _control()
    control["host_identity_sha256"] = producer.digest(_host_identity("opencode", current=True))
    events = [
        _event("opencode", kind, i, current=True)
        for i, kind in enumerate(("chat.message", "fork", "chat.message"))
    ]
    from benchmarks.hosts.v013_native_event_adapter import adapt_opencode_public_fork_observation
    from tests.test_v013_native_event_adapter import _public_fork_proof

    proof = _public_fork_proof()
    process = proof["process_binding"]
    process.update(
        status="running",
        exit_code=None,
        run_binding={
            "evidence_run_id": control["workflow_run_id"],
            "qualification_run_id": control["workflow_run_id"],
        },
        host_binary={
            "version": events[1]["host_identity"]["version"],
            "sha256": events[1]["host_identity"]["executable_sha256"],
        },
        candidate_binding=control["candidate_binding"],
        run_id=control["run_id"],
        host_identity_sha256=control["host_identity_sha256"],
        host_identity_source_sha256=control["host_identity_source_sha256"],
        nonce_sha256=control["nonce_sha256"],
    )
    process["broker_source"]["sha256"] = control["broker_source_sha256"]
    adapted = adapt_opencode_public_fork_observation(
        proof,
        host_identity=events[1]["host_identity"],
        execution_identity=events[1]["execution_identity"],
        route=events[1]["route"],
        event_sequence=1,
    )
    events[1] = adapted["event"]
    events[0]["session_sha256"] = events[1]["parent_session_sha256"]
    events[2]["session_sha256"] = events[1]["session_sha256"]
    turns = []
    target = {
        "kind": "knowledge",
        "knowledge_id": "knowledge_" + "a" * 24,
        "revision_id": "knowledgerev_" + "b" * 24,
    }
    for index, event_index in ((1, 0), (2, 2)):
        calls = []
        for ordinal, operation in enumerate(("query", "read")):
            calls.append(
                {
                    "turn": str(index),
                    "operation": operation,
                    "arguments_sha256": "a" * 64,
                    "arguments_bytes": 100,
                    "target": target if operation == "read" else None,
                    "response_target": target if operation == "read" else None,
                    "content_sha256": "b" * 64,
                    "content_bytes": 100,
                    "structured_sha256": "c" * 64,
                    "structured_bytes": 200,
                    "result_sha256": "d" * 64,
                    "result_bytes": 400,
                    "targets": [target],
                    "call_id_sha256": producer.digest(f"{index}-{ordinal}".encode()),
                    "message_sha256": "e" * 64,
                    "session_sha256": events[event_index]["session_sha256"],
                    "native_part_sha256": "f" * 64,
                    "correlation": "opencode_completed_tool_part_and_mcp_proxy",
                }
            )
        turns.append(
            {
                "index": index,
                "event_index": event_index,
                "session_sha256": events[event_index]["session_sha256"],
                "message_sha256": "e" * 64,
                "calls": calls,
                "native_context_sha256": "a" * 64,
                "native_context_bytes": 500,
                "next_action_sha256": "a" * 64,
                "next_action_matches": True,
                "decision_sha256": "b" * 64,
                "decision_matches": True,
                "ledger_before": "c" * 64,
                "ledger_after": "c" * 64,
                "elapsed_ms": 10.0,
                "rss_peak_bytes": 1024,
                "usage": dict(
                    input_tokens=100, output_tokens=20, cache_tokens=0, reasoning_tokens=0
                ),
            }
        )
    from deeplaw.knowledge_mcp_server import knowledge_tool_definition

    tool = knowledge_tool_definition(autonomous=True).model_dump(by_alias=True, exclude_none=True)
    observation = {
        "schema_version": producer.OBSERVATION,
        "control": control,
        "host": "opencode",
        "task_case": "continuity",
        "advertisement": producer.advertised_receipt([tool]),
        "turns": turns,
        "guard_requests": [{"sha256": "a" * 64, "bytes": 100}],
        "guard_rejected": 0,
        "public_fork_receipts": [adapted["public_fork_proof"]],
        "session_create": {
            "request_sha256": "a" * 64,
            "response_sha256": "b" * 64,
            "title": "DeepLaw supervised continuity",
            "observed": True,
        },
        "process_exit": {"host_exit_code": -15, "guard_exit_code": 0, "formal_v2_eligible": False},
        "cleanup_confirmed": True,
        "measurement_scope": "host_turn_visible_process_tree_sampled_rss_guard_route_only",
        "claim_eligible": False,
    }
    if wrong_response_target:
        from benchmarks.hosts.run_v013_host_task_qualification import HostTaskQualificationError

        another = {**target, "revision_id": "knowledgerev_" + "c" * 24}
        turns[0]["calls"][0]["targets"].append(another)
        turns[0]["calls"][1]["response_target"] = another
        with pytest.raises(HostTaskQualificationError, match="rejected"):
            producer.write_sources(
                tmp_path / "evidence", {}, observation, events,
                [derive_native_host_receipt(event) for event in events],
                [adapted["public_fork_proof"]],
            )
        return
    manifest = producer.write_sources(
        tmp_path / "evidence",
        {},
        observation,
        events,
        [derive_native_host_receipt(event) for event in events],
        [adapted["public_fork_proof"]],
    )
    result = producer.reopen(manifest)
    assert result["metrics"]["first_correct_action_rate"] == 1.0
    assert result["metrics"]["decision_preservation_rate"] == 1.0
    assert result["metrics"]["provider_bytes"] == 400
    assert result["status"] == "failed"
    assert sum(result["hard_failure_counts"].values()) > 0
    assert result["metrics"]["wrong_state_admission_count"] is None


def test_generated_privacy_wrapper_preserves_native_hooks_and_rejects_unknown_env(tmp_path):
    import shutil
    import subprocess

    node = shutil.which("node")
    if node is None:
        pytest.skip("Node unavailable for generated wrapper syntax and hook test")
    native = tmp_path / "native.mjs"
    native.write_text(
        "export default {server: async () => ({event: () => 42, "
        '"experimental.chat.system.transform": async (i, o) => '
        '{o.system.push("native governed capsule")}})};'
    )
    wrapper = tmp_path / "wrapper.mjs"
    wrapper.write_text(
        producer.privacy_wrapper(
            "./native.mjs", directory="/synthetic/task", worktree="/synthetic/task"
        )
    )
    block = (
        "<env>\n  Working directory: /synthetic/task\n"
        "  Workspace root folder: /synthetic/task\n  Is directory a git repo: yes\n"
        "  Platform: darwin\n  Today's date: Wed Sep 09 2026\n</env>"
    )
    script = (
        "import p, {transform} from " + json.dumps(wrapper.as_uri()) + ";"
        "const block = " + json.dumps(block) + ";"
        'const hooks = await p.server({}); const output={system:["before\\n"+block+"\\nafter"]};'
        'await hooks["experimental.chat.system.transform"]({},output);'
        'if(hooks.event()!==42 || output.system[1]!=="native governed capsule" || '
        'output.system[0].includes("/synthetic/") || !output.system[0].endsWith("after")) '
        'throw new Error("preservation failed");'
        'for(const bad of [[block,block],[block.replace("Platform:","Unknown:")],'
        '[block+"<available_references>"],[block.replaceAll("/synthetic/task","/unknown")]]) {'
        "let rejected=false;try{transform(bad)}catch{rejected=true} "
        'if(!rejected)throw new Error("unknown env accepted");}'
    )
    result = subprocess.run(
        [node, "--input-type=module", "-e", script], capture_output=True, timeout=10, check=False
    )
    assert result.returncode == 0, result.stderr.decode()


def test_proxy_reconnection_cannot_reset_budget(tmp_path, monkeypatch):
    import deeplaw.closed_mcp_launcher as launcher

    reached = []

    def unavailable(**kwargs):
        reached.append(kwargs)
        raise producer.ProducerError("synthetic child unavailable")

    monkeypatch.setattr(launcher, "closed_mcp_environment", unavailable)
    config = tmp_path / "proxy.json"
    producer.write_json(config, _proxy_launch_input(tmp_path, monkeypatch))
    with pytest.raises(producer.ProducerError, match="synthetic child"):
        producer.proxy(config)
    with pytest.raises(FileExistsError):
        producer.proxy(config)
    assert len(reached) == 1


def test_launch_prefix_requires_exact_executable_and_resource_hashes(tmp_path):
    executable = tmp_path / "sandbox-exec"
    profile = tmp_path / "profile.sb"
    executable.write_bytes(b"synthetic executable")
    profile.write_bytes(b"synthetic profile")
    value = {
        "argv": [str(executable), "-f", str(profile)],
        "files": [
            {"path": str(path), "sha256": producer.digest(path.read_bytes())}
            for path in (executable, profile)
        ],
    }
    producer.validate_launch_prefix(value)
    profile.write_bytes(b"changed")
    with pytest.raises(producer.ProducerError, match="changed"):
        producer.validate_launch_prefix(value)


def test_missing_usage_is_not_observed_zero():
    with pytest.raises(producer.ProducerError, match="missing"):
        producer.measure_usage([], "ses_synthetic")
    value = {
        "schema_version": "deeplaw.opencode-model-observation/v1",
        "session_sha256": producer.digest(b"ses_synthetic"),
        "message_sha256": "a" * 64,
        "provider_id": "deepseek",
        "model_id": "deepseek-v4-flash",
        "summary": False,
        "tokens": {"input": None, "output": 0, "reasoning": 0, "cache": {"read": 0}},
    }
    with pytest.raises(producer.ProducerError, match="unreported"):
        producer.measure_usage([value], "ses_synthetic")


def test_external_install_closure_is_hash_checked_and_importable(tmp_path, monkeypatch):
    import os
    import subprocess
    import sys

    destination = tmp_path / "deployment"
    producer.install(destination)
    monkeypatch.setattr(producer, "ROOT", destination)
    receipt = producer.verify_deployment()
    assert all(not row["path"].startswith(("src/", "var/")) for row in receipt["files"])
    check = subprocess.run(
        [
            sys.executable,
            "-c",
            "from benchmarks.hosts import opencode_single_task_producer as p; "
            "from benchmarks.hosts import run_pass13_opencode_continuity_qualification; "
            "from benchmarks.release import typed_qualification_evidence; p.verify_deployment()",
        ],
        cwd=destination,
        env={"PATH": os.defpath, "PYTHONPATH": str(destination)},
        capture_output=True,
        timeout=20,
        check=False,
    )
    assert check.returncode == 0, check.stderr.decode()
    path = destination / receipt["files"][0]["path"]
    path.write_bytes(path.read_bytes() + b" ")
    with pytest.raises(producer.ProducerError, match="changed"):
        producer.verify_deployment()


def test_fork_cloned_messages_do_not_count_as_new_usage():
    session = "ses_child"

    def observed(message, count):
        return {
            "schema_version": "deeplaw.opencode-model-observation/v1",
            "session_sha256": producer.digest(session.encode()),
            "message_sha256": producer.digest(message.encode()),
            "provider_id": "deepseek",
            "model_id": "deepseek-v4-flash",
            "summary": False,
            "tokens": {"input": count, "output": count, "reasoning": 0, "cache": {"read": 0}},
        }

    rows = [observed("cloned-parent-message", 10000), observed("new-child-message", 20)]
    usage, selected = producer.measure_usage(
        rows, session, message_sha256s={producer.digest(b"new-child-message")}
    )
    assert usage["input_tokens"] == usage["output_tokens"] == 20
    assert selected["message_sha256"] == producer.digest(b"new-child-message")


@pytest.mark.parametrize("location", ["deployment", "task", "repository"])
def test_guard_rejects_key_inside_runtime_or_repository_before_loading(
    tmp_path, monkeypatch, location
):
    deployment = tmp_path / "deployment"
    task = tmp_path / "task"
    repository = tmp_path / "repository"
    for path in (deployment, task, repository):
        path.mkdir()
    (repository / ".git").mkdir()
    key = {"deployment": deployment, "task": task, "repository": repository}[location] / "key"
    key.write_text("synthetic-key-not-an-actual-credential")
    key.chmod(0o600)
    config = task / "guard.json"
    producer.write_json(config, {"key_file": str(key), "nonce": "synthetic-nonce"})
    monkeypatch.setattr(producer, "ROOT", deployment)
    with pytest.raises(producer.ProducerError, match="outside"):
        producer.guard_process(config)


@pytest.mark.parametrize("outcome", ["first_failure", "later_failure", "success"])
def test_rss_sampler_fails_closed_without_thread_exception_output(monkeypatch, capsys, outcome):
    from types import SimpleNamespace
    from unittest.mock import Mock

    sampler = producer.RssSampler(42)
    unhandled = []
    monkeypatch.setattr(producer.threading, "excepthook", unhandled.append)
    first = SimpleNamespace(stdout=b"42 1 123\n")
    failure = RuntimeError("synthetic ps failure")
    if outcome == "first_failure":
        probe = Mock(side_effect=[failure])
    elif outcome == "later_failure":
        probe = Mock(side_effect=[first, failure])
    else:
        snapshots = iter([first, SimpleNamespace(stdout=b"42 1 150\n43 42 10\n")])

        def successful_probe(*args, **kwargs):
            snapshot = next(snapshots)
            if snapshot is not first:
                sampler.stopping.set()
            return snapshot

        probe = Mock(side_effect=successful_probe)
    monkeypatch.setattr(producer.subprocess, "run", probe)
    sampler.thread.start()
    sampler.thread.join(timeout=2)
    assert not sampler.thread.is_alive()
    if outcome == "success":
        assert sampler.stop() == 160 * 1024
    else:
        with pytest.raises(producer.ProducerError, match="RSS observation unavailable"):
            sampler.stop()
    assert probe.call_count == (1 if outcome == "first_failure" else 2)
    assert unhandled == []
    assert capsys.readouterr().err == ""


def _transport_result(operation, target=None, extra_bytes=0):
    boundary = {"legal_authority": False, "official_legal_sources_tool": "law_support",
                "persistent_writes": "separate_explicit_knowledge_sink",
                "case_data_allowed": False, "authority_from_ranking": False}
    if operation == "query":
        result = {"capsule": {"knowledge_id": "knowledge_" + "a" * 24,
                              "knowledge_revision_id": "knowledgerev_" + "b" * 24}}
        if extra_bytes:
            result["extra"] = "x" * extra_bytes
    else:
        result = {
            "target": target,
            "content": "public",
            "content_sha256": producer.digest(b"public"),
            "offset": 0,
            "next_offset": None,
            "total_characters": 6,
            "source_refs": [],
            "governance": {
                "origin": "agent_derived",
                "authority": "derived",
                "verification": "unverified",
                "lifecycle": "active",
                "scope": "project",
                "sensitivity": "public",
                "legal_authority": False,
            },
            "locator": None,
            "write_performed": False,
            "budget": {
                "read_calls": 1,
                "read_content_bytes": 6,
                "max_read_calls": 32,
                "max_read_content_bytes": 262144,
                "scope": "mcp_lifespan_successful_read_content_only",
            },
        }
    outer = {
        "schema_version": "deeplaw.knowledge-support-output/"
        + ("v6" if operation == "query" else "v7"),
        "operation": operation,
        "authority_boundary": boundary,
        "result": result,
    }
    producer.contract("knowledge-support.output.v7.schema.json", outer)
    text = producer.canonical_json(result["capsule"] if operation == "query" else outer)
    return {"content": [{"type": "text", "text": text}], "structuredContent": outer}


@pytest.mark.parametrize("wrong_target", [False, True])
def test_read_response_must_equal_requested_target_not_another_discovered_target(wrong_target):
    first = {
        "kind": "knowledge",
        "knowledge_id": "knowledge_" + "a" * 24,
        "revision_id": "knowledgerev_" + "b" * 24,
    }
    second = {**first, "revision_id": "knowledgerev_" + "c" * 24}
    budget = producer.ReadBudget()
    budget.request(
        {"operation": "query", "query": "public", "scope": "project", "max_sensitivity": "public"}
    )
    budget.targets = [first, second]
    budget.request(
        {
            "operation": "read",
            "target": first,
            "scope": "project",
            "max_sensitivity": "public",
            "max_chars": 4000,
        }
    )
    response = _transport_result("read", second if wrong_target else first)
    if wrong_target:
        with pytest.raises(producer.ProducerError, match="target"):
            budget.response(response, expected_target=first)
    else:
        budget.response(response, expected_target=first)


@pytest.mark.parametrize("extra_bytes", [0, 210000])
def test_complete_call_tool_result_is_bounded_before_transport(extra_bytes):
    budget = producer.ReadBudget()
    budget.request(
        {"operation": "query", "query": "public", "scope": "project", "max_sensitivity": "public"}
    )
    response = _transport_result("query", extra_bytes=extra_bytes)
    if extra_bytes:
        assert len(producer.encoded(response)) > 197632
        with pytest.raises(producer.ProducerError, match="CallToolResult"):
            budget.response(response)
    else:
        assert budget.response(response)["result_bytes"] < 197632


def _proxy_launch_input(tmp_path, monkeypatch):
    entry = tmp_path / "deeplaw"
    entry.write_bytes(b"synthetic entry")
    prefix = {"argv": [], "files": []}
    config = {
        "deeplaw": str(entry),
        "vault": str(tmp_path / "vault"),
        "proxy_claim": str(tmp_path / "claim.json"),
        "proxy_log": str(tmp_path / "log.jsonl"),
        "turn_state": str(tmp_path / "turn.json"),
        "nonce_sha256": "a" * 64,
        "mcp_launch_prefix": prefix,
        "expected_launch": {
            "prefix_sha256": producer.digest(prefix),
            "source_closure_sha256": "b" * 64,
            "deeplaw_sha256": producer.digest(entry.read_bytes()),
        },
    }
    monkeypatch.setattr(producer, "verify_deployment", lambda: {"source_closure_sha256": "b" * 64})
    return config


@pytest.mark.parametrize("drift", ["prefix", "closure", "entry"])
def test_proxy_rejects_drift_against_owner_frozen_launch_before_child(tmp_path, monkeypatch, drift):
    from pathlib import Path

    import deeplaw.closed_mcp_launcher as launcher

    config = _proxy_launch_input(tmp_path, monkeypatch)
    if drift == "prefix":
        executable = Path(config["deeplaw"])
        config["mcp_launch_prefix"] = {
            "argv": [str(executable)],
            "files": [
                {"path": str(executable), "sha256": producer.digest(executable.read_bytes())}
            ],
        }
    elif drift == "closure":
        monkeypatch.setattr(
            producer, "verify_deployment", lambda: {"source_closure_sha256": "c" * 64}
        )
    else:
        Path(config["deeplaw"]).write_bytes(b"changed entry")

    def no_child(**kwargs):
        raise AssertionError("unverified launch reached child environment")

    monkeypatch.setattr(launcher, "closed_mcp_environment", no_child)
    path = tmp_path / "proxy.json"
    producer.write_json(path, config)
    with pytest.raises(producer.ProducerError):
        producer.proxy(path)


def test_install_rejects_symlink_ancestor_before_directory_creation(tmp_path, monkeypatch):
    real = tmp_path / "real"
    real.mkdir()
    alias = tmp_path / "alias"
    alias.symlink_to(real, target_is_directory=True)
    monkeypatch.setattr(producer, "deployment_sources", lambda: [])
    with pytest.raises(producer.ProducerError, match="ancestor"):
        producer.install(alias / "deployment")
    assert not (real / "deployment").exists()


@pytest.mark.parametrize(
    "ending,cleanup",
    [
        ("eof", True),
        ("error", True),
        ("sigint", True),
        ("sigterm", True),
        ("eof", False),
        ("wait_timeout", True),
        ("response_error", True),
    ],
)
def test_proxy_cleanup_on_eof_error_signal_and_unconfirmed_cleanup(
    tmp_path, monkeypatch, ending, cleanup
):
    import io
    from contextlib import nullcontext
    from types import SimpleNamespace
    from unittest.mock import Mock

    from deeplaw import bounded_subprocess as bounded
    from deeplaw import closed_mcp_launcher as launcher

    config = _proxy_launch_input(tmp_path, monkeypatch)
    path = tmp_path / "proxy.json"
    producer.write_json(path, config)
    input_seen = producer.threading.Event()

    class Output:
        def readline(self, maximum):
            assert input_seen.wait(timeout=1)
            return b"not-json\n" if ending == "response_error" else b""

    child = SimpleNamespace(
        stdin=io.BytesIO(),
        stdout=Output(),
        stderr=None,
        pid=42,
        wait=Mock(return_value=0),
        kill=Mock(),
        terminate=Mock(),
    )
    if ending == "wait_timeout":
        child.wait.side_effect = producer.subprocess.TimeoutExpired("synthetic", 5)
    guard = object()
    spawn = Mock(return_value=(child, guard))
    monkeypatch.setattr(bounded, "spawn_process", spawn)
    monkeypatch.setattr(producer.subprocess, "Popen", Mock(return_value=child))
    kill = Mock(return_value=cleanup)
    monkeypatch.setattr(bounded, "_kill", kill)
    monkeypatch.setattr(
        launcher,
        "closed_mcp_environment",
        lambda **kwargs: nullcontext(SimpleNamespace(cwd=tmp_path, environment={})),
    )
    originals = {producer.signal.SIGINT: object(), producer.signal.SIGTERM: object()}
    handlers = dict(originals)
    monkeypatch.setattr(producer.signal, "getsignal", handlers.__getitem__)

    def register(signum, handler):
        assert producer.threading.current_thread() is producer.threading.main_thread()
        handlers[signum] = handler

    monkeypatch.setattr(producer.signal, "signal", register)

    class Input:
        def readline(self, maximum):
            input_seen.set()
            if ending == "error":
                raise RuntimeError("synthetic stdin error")
            if ending in {"sigint", "sigterm"}:
                signum = producer.signal.SIGINT if ending == "sigint" else producer.signal.SIGTERM
                assert callable(handlers[signum]), "signal cleanup handler missing"
                handlers[signum](signum, None)
            return b""

    monkeypatch.setattr(producer.sys, "stdin", SimpleNamespace(buffer=Input()))
    if ending == "eof" and cleanup:
        producer.proxy(path)
    else:
        with pytest.raises((producer.ProducerError, RuntimeError)):
            producer.proxy(path)
    spawn.assert_called_once()
    if producer.os.name == "posix":
        assert spawn.call_args.kwargs["start_new_session"] is True
    kill.assert_called_once_with(child, guard)
    child.wait.assert_called_once_with(timeout=5)
    assert handlers == originals
    child.terminate.assert_not_called()


@pytest.mark.parametrize("changed_entry", ["deeplaw", "node"])
def test_run_rechecks_prepared_entry_bytes_before_guard_or_host(
    tmp_path, monkeypatch, changed_entry
):
    from unittest.mock import Mock

    root = tmp_path / "task"
    root.mkdir()
    repository = root / "repo"
    original = repository / ".opencode/supervised-source/deeplaw-native.ts"
    wrapper = repository / ".opencode/plugins/deeplaw-native.ts"
    for path in (original, wrapper):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"synthetic plugin")
    entries = {name: tmp_path / name for name in ("deeplaw", "node", "opencode")}
    for path in entries.values():
        path.write_bytes(b"synthetic entry")
    entry_hash = producer.digest(b"synthetic entry")
    prepared = {
        "root": str(root),
        "repository": str(repository),
        **{key: str(path) for key, path in entries.items()},
        "runtime_entry_hashes": {"deeplaw": entry_hash, "node": entry_hash},
        "deployment_sha256": "b" * 64,
        "mcp_launch_prefix_sha256": producer.digest({"argv": [], "files": []}),
        "host_identity": {"executable_sha256": entry_hash},
        "host_identity_source_sha256": "c" * 64,
        "privacy": {
            "privacy_wrapper_sha256": producer.digest(wrapper.read_bytes()),
            "original_plugin_sha256": producer.digest(original.read_bytes()),
        },
        "run_id": "synthetic-runtime-drift",
        "workflow_run_id": 1,
        "candidate_binding": _control()["candidate_binding"],
        "key_file": "unused",
    }
    path = root / "prepared.json"
    producer.write_json(path, prepared)
    entries[changed_entry].write_bytes(b"changed entry")
    monkeypatch.setattr(producer, "verify_deployment", lambda: {"source_closure_sha256": "b" * 64})
    guard_start = Mock(side_effect=AssertionError("unverified runtime reached guard"))
    monkeypatch.setattr(producer.ExternalGuard, "start", guard_start)
    monkeypatch.setattr(producer.ExternalGuard, "stop", lambda self: None)
    with pytest.raises(producer.ProducerError, match="execution bytes changed"):
        producer.run(path)
    guard_start.assert_not_called()


@pytest.mark.parametrize(
    "mutation", ["run_binding", "host_binary", "host_identity", "same_session"]
)
def test_reopen_rejects_fork_binding_tampering(tmp_path, monkeypatch, mutation):
    from copy import deepcopy

    original = producer.validate_host_observation
    captured = []

    def capture(value, **kwargs):
        captured.append((deepcopy(value), deepcopy(kwargs)))
        return original(value, **kwargs)

    monkeypatch.setattr(producer, "validate_host_observation", capture)
    test_synthetic_two_turn_sources_reopen_without_formal_promotion(tmp_path, False)
    value, kwargs = captured[-1]
    proof = value["public_fork_receipts"][0]
    fork = next(event for event in kwargs["events"] if event["event_type"] == "fork")
    if mutation == "run_binding":
        proof["process_binding"]["run_binding"]["qualification_run_id"] += 1
    elif mutation == "host_binary":
        proof["process_binding"]["host_binary"]["sha256"] = "e" * 64
    elif mutation == "host_identity":
        fork["host_identity"]["version"] = "changed-version"
        proof["process_binding"]["host_binary"]["version"] = "changed-version"
    else:
        proof["parent_session_sha256"] = proof["child_session_sha256"]
        fork["parent_session_sha256"] = fork["session_sha256"]
    with pytest.raises(producer.ProducerError, match=r"fork (run|binary|identity|sessions)"):
        original(value, **kwargs)


def _guard_handler(monkeypatch, guard, opener, body=None):
    import io
    from unittest.mock import Mock

    captured = {}

    def server(address, handler):
        captured["handler"] = handler
        return Mock(server_address=("127.0.0.1", 12345))

    monkeypatch.setattr("http.server.ThreadingHTTPServer", server)
    monkeypatch.setattr(producer.threading, "Thread", Mock())
    monkeypatch.setattr(producer.urllib.request, "build_opener", lambda *args: opener)
    guard.start()
    handler = object.__new__(captured["handler"])
    raw = _guard_body() if body is None else body
    handler.headers = {"Content-Length": str(len(raw)), "Authorization": "Bearer synthetic-nonce"}
    handler.path = "/chat/completions"
    handler.rfile = io.BytesIO(raw)
    handler.wfile = io.BytesIO()
    handler.send_response = Mock()
    handler.send_header = Mock()
    handler.end_headers = Mock()
    return handler


@pytest.mark.parametrize("kind", ["inspect", "http", "network", "timeout", "write"])
def test_guard_failure_retains_fixed_stage_without_exception_payload(monkeypatch, kind):
    import urllib.error
    from unittest.mock import MagicMock

    canary = "private-test-key synthetic-nonce /private/secret prompt reasoning"
    opener = MagicMock()
    response = opener.open.return_value.__enter__.return_value
    response.read.return_value = b"{}"
    response.headers = {}
    if kind == "http":
        opener.open.side_effect = urllib.error.HTTPError(canary, 429, canary, {}, None)
    elif kind == "network":
        opener.open.side_effect = urllib.error.URLError(canary)
    elif kind == "timeout":
        opener.open.side_effect = TimeoutError(canary)
    guard = producer.RequestGuard(key="private-test-key", nonce="synthetic-nonce", forward=True)
    guard.active = kind != "inspect"
    handler = _guard_handler(monkeypatch, guard, opener)
    if kind == "write":
        handler.wfile = MagicMock()
        handler.wfile.write.side_effect = BrokenPipeError(canary)
    handler.do_POST()
    receipt = guard.snapshot()
    assert receipt["rejected"] == 1
    expected = {
        "inspect": ("inspect", "inactive_turn"), "http": ("provider_open", "http_error"),
        "network": ("provider_open", "network_error"),
        "timeout": ("provider_open", "timeout"), "write": ("client_write", "io_error"),
    }
    assert (receipt["first_failure"]["stage"], receipt["first_failure"]["code"]) == expected[kind]
    assert receipt["first_failure"].get("http_status") == (429 if kind == "http" else None)
    assert canary not in producer.canonical_json(receipt)
    assert len(receipt["requests"]) == (0 if kind == "inspect" else 1)


def test_failure_cleanup_preserves_first_and_persists_after_all_attempts(tmp_path):
    events = []
    failure = producer.FailureEvidence()
    failure.capture(TimeoutError("secret body"), "host_turn")

    def bad():
        events.append("failed_cleanup")
        raise ValueError("key nonce /private/path")

    failure.cleanup("sampler_cleanup", bad)
    failure.cleanup("guard_cleanup", lambda: events.append("guard_cleanup"))
    failure.persist(tmp_path, guard=None, binding_sha256="a" * 64)
    receipt = json.loads((tmp_path / "failure.json").read_text())
    assert events == ["failed_cleanup", "guard_cleanup"]
    assert receipt["first_failure"] == {"stage": "host_turn", "code": "timeout"}
    assert receipt["cleanup_failures"] == [{"stage": "sampler_cleanup", "code": "internal_error"}]
    assert receipt["cleanup_confirmed"] is False
    assert receipt["formal_admission"] is False
    with pytest.raises(producer.ProducerError, match="host_turn:timeout"):
        failure.raise_if_failed()
    assert "secret" not in producer.canonical_json(receipt)


def test_external_guard_keeps_rejection_and_marks_failed_ipc_stale(monkeypatch):
    from pathlib import Path
    from unittest.mock import Mock

    guard = producer.ExternalGuard(Path("unused"))
    guard.process = Mock()
    snapshot = producer.RequestGuard(key="synthetic", nonce="synthetic").snapshot()
    snapshot.update(rejected=1, first_failure={"stage": "inspect", "code": "request_shape"})
    monkeypatch.setattr(guard, "_response", Mock(side_effect=[snapshot, TimeoutError("secret")]))
    with pytest.raises(producer.DiagnosticError, match="inspect:request_shape"):
        guard.active(False)
    assert guard.receipt == snapshot and guard.receipt_current
    with pytest.raises(producer.DiagnosticError, match="guard_protocol:timeout"):
        guard.active(False)
    assert guard.receipt == snapshot and not guard.receipt_current


@pytest.mark.parametrize("mutation", ["payload", "requests", "status", "unknown_code"])
def test_guard_snapshot_rejects_unbounded_or_untrusted_fields(mutation):
    snapshot = producer.RequestGuard(key="synthetic", nonce="synthetic").snapshot()
    if mutation == "payload":
        snapshot["body"] = "secret"
    elif mutation == "requests":
        snapshot["requests"] = [{"sha256": "a" * 64, "bytes": 2}] * 7
    else:
        snapshot["first_failure"] = {"stage": "provider_open", "code": "http_error"}
        if mutation == "status":
            snapshot["first_failure"]["http_status"] = "secret"
        else:
            snapshot["first_failure"]["code"] = "secret"
    with pytest.raises(producer.ProducerError):
        producer.validate_guard_snapshot(snapshot)


def test_external_guard_cleanup_failure_does_not_erase_snapshot(monkeypatch):
    from pathlib import Path
    from unittest.mock import Mock

    guard = producer.ExternalGuard(Path("unused"))
    guard.process = Mock(returncode=1)
    guard.process.poll.return_value = None
    guard.process.kill.side_effect = OSError("secret kill path")
    snapshot = producer.RequestGuard(key="synthetic", nonce="synthetic").snapshot(
        cleanup_confirmed=False,
        cleanup_failure={"stage": "guard_cleanup", "code": "timeout"},
    )
    monkeypatch.setattr(guard, "_response", lambda: snapshot)
    with pytest.raises(producer.DiagnosticError, match="guard_cleanup:timeout"):
        guard.stop()
    assert guard.receipt == snapshot
    assert guard.cleanup_failures == [{"stage": "guard_cleanup", "code": "io_error"}]
    guard.process.stdin.close.assert_called_once()
    guard.process.stdout.close.assert_called_once()


def test_run_failure_receipt_survives_all_cleanup_failures(tmp_path, monkeypatch):
    from unittest.mock import Mock

    from benchmarks.hosts import run_pass13_opencode_continuity_qualification as legacy

    root = tmp_path / "task"
    repository = root / "repo"
    for relative in (".opencode/plugins/deeplaw-native.ts",
                     ".opencode/supervised-source/deeplaw-native.ts"):
        path = repository / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"synthetic")
    prepared = {
        "root": str(root), "repository": str(repository), "deeplaw": "unused",
        "opencode": "unused", "deployment_sha256": "a" * 64,
        "mcp_launch_prefix_sha256": producer.digest({"argv": [], "files": []}),
        "host_identity": {"executable_sha256": "a" * 64},
        "host_identity_source_sha256": "a" * 64,
        "privacy": {key: producer.digest(b"synthetic") for key in (
            "privacy_wrapper_sha256", "original_plugin_sha256")},
        "run_id": "synthetic", "workflow_run_id": 1,
        "candidate_binding": _control()["candidate_binding"],
        "runtime_entry_hashes": {"deeplaw": "a" * 64}, "key_file": "unused",
        "environment": {"OPENCODE_CONFIG": str(root / "config.json"),
                        "DEEPLAW_OPENCODE_MODEL_RECEIPT": str(root / "native.jsonl")},
        "selector_source_symlink": False, "fixture": {"task_handle": "x", "grant_id": "y"},
        "primary_binding": {key: "a" * 64 for key in (
            "project_sha256", "repository_sha256", "worktree_sha256")},
    }
    config = {"provider": {"deepseek": {"options": {}}}, "agent": {"qualification": {}},
              "mcp": {"deeplaw_knowledge": {}}}
    monkeypatch.setattr(producer, "read_json", lambda path: config if path.name == "config.json"
                        else prepared)
    monkeypatch.setattr(producer, "verify_deployment", lambda: {"source_closure_sha256": "a" * 64})
    monkeypatch.setattr(producer, "validate_runtime_entries", lambda value: None)
    monkeypatch.setattr(producer, "validate_control", lambda value: None)
    monkeypatch.setattr(producer, "exact_file", lambda path, sha: path)
    monkeypatch.setattr(producer, "json_lines", lambda path: [])
    monkeypatch.setattr(legacy, "_bind_public_host_session", lambda *args, **kwargs: None)
    monkeypatch.setattr(legacy, "_ledger_head", lambda *args, **kwargs: "a" * 64)
    monkeypatch.setattr("deeplaw.task_continuity.resolve_host_session", lambda **kwargs: {
        "status": "exact", "binding_sha256": "a" * 64, "task_handle_sha256": "a" * 64,
    })
    events = []

    def fail_cleanup(name):
        events.append(name)
        raise OSError("secret nonce /private/path")

    guard = producer.ExternalGuard(root / "unused")
    guard.url = "http://127.0.0.1:12345"
    guard.start = lambda: None
    guard.stop = lambda: fail_cleanup("guard")
    guard.active = lambda enabled: None if enabled else fail_cleanup("deactivate")
    monkeypatch.setattr(producer, "ExternalGuard", lambda config: guard)
    server = Mock()
    server.process.pid = 42
    server.stop.side_effect = lambda: fail_cleanup("host")
    monkeypatch.setattr(legacy, "_OpenCodeLocalServer", lambda **kwargs: server)
    sampler = Mock()
    sampler.stop.side_effect = lambda: fail_cleanup("sampler")
    monkeypatch.setattr(producer, "RssSampler", lambda pid: sampler)

    def state(path, value):
        if path.name == "turn-state.json" and not value["active"] and "api" in events:
            fail_cleanup("state")

    monkeypatch.setattr(producer, "replace_state", state)

    def api(server, method, route, body=None):
        if route == "/session":
            return {"id": "session-one", "title": body["title"]}, b"{}"
        if method == "GET":
            return [], b"[]"
        events.append("api")
        raise TimeoutError("secret prompt reasoning")

    monkeypatch.setattr(producer, "api", api)
    with pytest.raises(producer.DiagnosticError, match="host_turn:timeout"):
        producer.run(root / "prepared.json")
    receipt = json.loads((root / "failure.json").read_text())
    assert events == ["api", "sampler", "state", "deactivate", "host", "guard"]
    assert receipt["first_failure"] == {"stage": "host_turn", "code": "timeout"}
    assert len(receipt["cleanup_failures"]) == 5
    assert receipt["cleanup_confirmed"] is False
    text = producer.canonical_json(receipt)
    for forbidden in ("secret", "nonce", "/private/path", "prompt", "reasoning"):
        assert forbidden not in text


def test_guard_process_emits_failed_cleanup_receipt(tmp_path, monkeypatch, capsys):
    import io
    from unittest.mock import Mock

    task = tmp_path / "task"
    task.mkdir()
    key = tmp_path / "synthetic-key"
    key.write_text("synthetic-key-for-test-only")
    key.chmod(0o600)
    monkeypatch.setattr(producer, "read_json", lambda path: {
        "key_file": str(key), "nonce": "synthetic-nonce",
    })
    guard = producer.RequestGuard(key="synthetic", nonce="synthetic")
    guard.start = lambda: "http://127.0.0.1:12345"
    guard.stop = Mock(side_effect=TimeoutError("secret cleanup prompt"))
    monkeypatch.setattr(producer, "RequestGuard", lambda **kwargs: guard)
    monkeypatch.setattr(producer.sys, "stdin", io.StringIO('{"stop":true}\n'))
    with pytest.raises(producer.DiagnosticError, match="guard_cleanup:timeout"):
        producer.guard_process(task / "unused")
    lines = capsys.readouterr().out.splitlines()
    snapshot = producer.validate_guard_snapshot(json.loads(lines[-1]))
    assert snapshot["cleanup_confirmed"] is False
    assert snapshot["cleanup_failure"] == {"stage": "guard_cleanup", "code": "timeout"}
    assert "secret" not in lines[-1]
    guard.stop.assert_called_once()


@pytest.mark.parametrize("kind", ["invalid_json", "shape", "privacy", "budget", "oversize"])
def test_guard_inspection_and_response_bound_codes_remain_fail_closed(monkeypatch, kind):
    from unittest.mock import MagicMock

    opener = MagicMock()
    response = opener.open.return_value.__enter__.return_value
    response.read.return_value = b"x" * (producer.MAX_BYTES + 1) if kind == "oversize" else b"{}"
    response.headers = {}
    guard = producer.RequestGuard(key="private-test-key", nonce="synthetic-nonce", forward=True)
    guard.active = True
    body = _guard_body()
    if kind == "invalid_json":
        body = b"{"
    elif kind == "shape":
        body = b'{"unknown":true}'
    elif kind == "privacy":
        body = _guard_body("Working directory: /Users/synthetic/project")
    elif kind == "budget":
        guard.requests = [{"sha256": "a" * 64, "bytes": 2}] * 6
    handler = _guard_handler(monkeypatch, guard, opener, body)
    handler.do_POST()
    snapshot = guard.snapshot()
    expected = {
        "invalid_json": ("inspect", "invalid_json"), "shape": ("inspect", "request_shape"),
        "privacy": ("inspect", "privacy_rejected"), "budget": ("admission", "request_budget"),
        "oversize": ("provider_read", "response_bound"),
    }
    assert (snapshot["first_failure"]["stage"], snapshot["first_failure"]["code"]) == expected[kind]
    assert snapshot["rejected"] == 1
    assert len(snapshot["requests"]) <= 6
    if kind != "oversize":
        opener.open.assert_not_called()
    handler.send_response.assert_called_once_with(403)
    # A subsequent rejection increments the total without replacing the first cause.
    guard.reject(RuntimeError("do not retain this"), "ingress")
    assert guard.snapshot()["rejected"] == 2
    assert guard.snapshot()["first_failure"] == snapshot["first_failure"]


def test_failure_receipt_write_error_does_not_replace_primary(tmp_path, monkeypatch):
    from unittest.mock import Mock

    failure = producer.FailureEvidence()
    failure.capture(TimeoutError("secret"), "host_turn")
    monkeypatch.setattr(producer, "write_json", Mock(side_effect=OSError("private path")))
    failure.persist(tmp_path, guard=None, binding_sha256="a" * 64)
    with pytest.raises(producer.DiagnosticError, match="host_turn:timeout"):
        failure.raise_if_failed()


@pytest.mark.parametrize("rejected", [0, 1])
def test_guard_stop_observes_late_rejection_without_repeating_cleanup(monkeypatch, rejected):
    from pathlib import Path
    from unittest.mock import Mock

    guard = producer.ExternalGuard(Path("unused"))
    guard.process = Mock(returncode=0)
    guard.process.poll.return_value = 0
    snapshot = producer.RequestGuard(key="synthetic", nonce="synthetic").snapshot(
        cleanup_confirmed=True,
    )
    if rejected:
        snapshot.update(rejected=1, first_failure={"stage": "inspect", "code": "request_shape"})
    response = Mock(return_value=snapshot)
    monkeypatch.setattr(guard, "_response", response)
    if rejected:
        with pytest.raises(producer.DiagnosticError, match="inspect:request_shape"):
            guard.stop()
    else:
        guard.stop()
        guard.stop()
    assert guard.receipt == snapshot
    response.assert_called_once()
    guard.process.kill.assert_not_called()


def test_guard_unknown_inspection_failure_is_not_claimed_as_privacy_rejection(monkeypatch):
    from unittest.mock import MagicMock, Mock

    guard = producer.RequestGuard(key="private-test-key", nonce="synthetic-nonce", forward=True)
    guard.active = True
    handler = _guard_handler(monkeypatch, guard, MagicMock())
    monkeypatch.setattr(producer, "safe", Mock(side_effect=RuntimeError("private secret")))
    handler.do_POST()
    assert guard.snapshot()["first_failure"] == {"stage": "inspect", "code": "internal_error"}
    assert guard.snapshot()["in_flight"] == 0


def test_guard_does_not_confirm_cleanup_with_inflight_request():
    guard = producer.RequestGuard(key="synthetic", nonce="synthetic")
    guard.in_flight = 1
    with pytest.raises(producer.DiagnosticError, match="guard_cleanup:cleanup_unconfirmed"):
        guard.stop()
    with pytest.raises(producer.ProducerError):
        producer.validate_guard_snapshot(guard.snapshot(cleanup_confirmed=True))


@pytest.mark.parametrize("include_effort", [False, True])
def test_guard_accepts_optional_exact_max_reasoning_effort(include_effort):
    body = json.loads(_guard_body())
    if include_effort:
        body["reasoning_effort"] = "max"
    guard = producer.RequestGuard(key="private-test-key", nonce="synthetic-nonce")
    guard.active = True
    result = guard.inspect(
        producer.encoded(body), path="/chat/completions", authorization="Bearer synthetic-nonce",
    )
    assert result == body
    assert guard.requests == []  # Inspection is not a forwarded Provider request.


@pytest.mark.parametrize("effort", [None, False, 1, "high", "MAX", "", {}, ["max"]])
def test_guard_rejects_reasoning_effort_other_than_exact_max(effort):
    body = {**json.loads(_guard_body()), "reasoning_effort": effort}
    guard = producer.RequestGuard(key="private-test-key", nonce="synthetic-nonce")
    guard.active = True
    with pytest.raises(producer.DiagnosticError, match="inspect:reasoning_effort_invalid"):
        guard.inspect(
            producer.encoded(body),
            path="/chat/completions",
            authorization="Bearer synthetic-nonce",
        )
    assert guard.requests == []


@pytest.mark.parametrize("extra", [{"reasoningEffort": "max"}, {"unknown": "max"}])
def test_guard_max_effort_does_not_admit_unknown_or_camelcase_fields(extra):
    body = {**json.loads(_guard_body()), "reasoning_effort": "max", **extra}
    guard = producer.RequestGuard(key="private-test-key", nonce="synthetic-nonce")
    guard.active = True
    with pytest.raises(producer.DiagnosticError, match="inspect:request_shape"):
        guard.inspect(
            producer.encoded(body),
            path="/chat/completions",
            authorization="Bearer synthetic-nonce",
        )


def test_supervised_agent_overrides_only_prompt_and_steps():
    from copy import deepcopy

    from benchmarks.hosts import run_pass13_opencode_continuity_qualification as legacy

    original = legacy.build_opencode_config()
    config = deepcopy(original)
    producer._configure_supervised_agent(config)
    agent = config["agent"]["qualification"]
    assert agent["steps"] == 3
    assert producer.TOOL in agent["prompt"]
    assert "exactly twice: first operation query" in agent["prompt"]
    assert "then operation read with the exact knowledge reference" in agent["prompt"]
    assert "scope project, max_sensitivity public and max_chars 4000" in agent["prompt"]
    assert "Make no other tool calls" in agent["prompt"]
    assert "do not invoke any tool" not in agent["prompt"]
    expected = deepcopy(original)
    expected["agent"]["qualification"].update(prompt=agent["prompt"], steps=3)
    assert config == expected
    assert legacy.build_opencode_config() == original
    assert "do not invoke any tool" in original["agent"]["qualification"]["prompt"]
