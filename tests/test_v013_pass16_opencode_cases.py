from __future__ import annotations

import json
from pathlib import Path

import pytest

REPOSITORY = Path(__file__).resolve().parents[1]


def test_opencode_uses_the_frozen_three_case_matrix_and_neutral_prompts() -> None:
    import importlib

    pass16_continuity_cases = importlib.import_module(
        "benchmarks.hosts.pass16_continuity_cases"
    )
    runner = importlib.import_module(
        "benchmarks.hosts.run_pass13_opencode_continuity_qualification"
    )

    assert runner.SCENARIOS == ("cold_start", "resume_fork", "compaction_forget")
    loaded = pass16_continuity_cases.load_cases()
    assert loaded["model_outputs_seen_before_freeze"] is False
    assert tuple(row["scenario"] for row in loaded["task_cases"]) == runner.SCENARIOS
    for scenario in runner.SCENARIOS:
        prompt = runner.SCENARIO_TASKS[scenario]
        assert "gold" not in prompt.casefold()
        assert "score" not in prompt.casefold()
        assert "expected score" not in prompt.casefold()
        assert prompt == pass16_continuity_cases.candidate_prompt(
            pass16_continuity_cases.task_case(scenario)
        )


def test_markers_include_current_stale_wrong_task_wrong_worktree_and_forget() -> None:
    import importlib

    pass16_continuity_cases = importlib.import_module(
        "benchmarks.hosts.pass16_continuity_cases"
    )
    runner = importlib.import_module(
        "benchmarks.hosts.run_pass13_opencode_continuity_qualification"
    )

    for scenario in runner.SCENARIOS:
        case = pass16_continuity_cases.task_case(scenario)
        markers = pass16_continuity_cases.marker_values(case)
        assert {"current", "stale", "wrong_task_line", "wrong_worktree"} <= set(markers)
        if scenario == "compaction_forget":
            assert "forgotten" in markers
        assert all(marker.startswith("PASS16-") for marker in markers.values())
        assert set(pass16_continuity_cases.forbidden_markers(case)) == {
            markers["stale"],
            markers["wrong_task_line"],
            markers["wrong_worktree"],
        }


def test_git_binding_uses_real_oid_and_path_free_digests(tmp_path: Path) -> None:
    import importlib
    import subprocess

    from jsonschema import Draft202012Validator

    pass16_continuity_cases = importlib.import_module(
        "benchmarks.hosts.pass16_continuity_cases"
    )

    repository = tmp_path / "task"
    repository.mkdir()
    def run(*args: str) -> None:
        completed = subprocess.run(
            ["git", *args],
            cwd=repository,
            capture_output=True,
            check=False,
            text=True,
        )
        assert completed.returncode == 0, completed.stderr


    run("init", "--quiet")
    run("config", "user.email", "qualification@localhost")
    run("config", "user.name", "DeepLaw Qualification")
    (repository / "TASK.md").write_text("task\n", encoding="utf-8")
    run("add", "TASK.md")
    run("commit", "--quiet", "-m", "initial")
    binding = pass16_continuity_cases.git_binding(repository, task_line="cold_start")
    assert len(binding["base_revision"]) == 40
    assert binding["base_revision"] == run_git(repository, "rev-parse", "HEAD")
    assert str(repository) not in json.dumps(binding)
    assert pass16_continuity_cases.binding_sha256(binding) == binding["binding_sha256"]
    schema = json.loads(
        (REPOSITORY / "contracts/task-context-binding.v1.schema.json").read_text(
            encoding="utf-8"
        )
    )
    Draft202012Validator(schema).validate(binding)


def test_concurrent_worktree_changes_only_the_worktree_route_component(
    tmp_path: Path,
) -> None:
    import importlib
    import subprocess

    cases = importlib.import_module("benchmarks.hosts.pass16_continuity_cases")
    repository = tmp_path / "task"
    repository.mkdir()

    def git(*args: str, cwd: Path = repository) -> None:
        completed = subprocess.run(
            ["git", *args], cwd=cwd, capture_output=True, check=False, text=True
        )
        assert completed.returncode == 0, completed.stderr

    git("init", "--quiet")
    git("config", "user.email", "qualification@localhost")
    git("config", "user.name", "DeepLaw Qualification")
    (repository / "TASK.md").write_text("task\n", encoding="utf-8")
    git("add", "TASK.md")
    git("commit", "--quiet", "-m", "initial")
    concurrent = tmp_path / "concurrent"
    git("worktree", "add", "--quiet", "--detach", str(concurrent))
    primary = cases.git_binding(repository, task_line="same-task")
    other = cases.git_binding(repository, task_line="same-task", worktree=concurrent)
    assert primary["project_sha256"] == other["project_sha256"]
    assert primary["task_lineage_sha256"] == other["task_lineage_sha256"]
    assert primary["repository_sha256"] == other["repository_sha256"]
    assert primary["base_revision"] == other["base_revision"]
    assert primary["worktree_sha256"] != other["worktree_sha256"]


def test_runner_owned_vault_and_wrapper_do_not_change_the_frozen_binding(
    tmp_path: Path,
) -> None:
    import importlib

    runner = importlib.import_module(
        "benchmarks.hosts.run_pass13_opencode_continuity_qualification"
    )
    cases = importlib.import_module("benchmarks.hosts.pass16_continuity_cases")
    repository, _concurrent, before, _alternate = runner._create_git_task_repository(
        tmp_path, task_line="same-task"
    )
    (repository / "deeplaw-closed-mcp").write_text("wrapper\n", encoding="utf-8")
    (repository / "vault").mkdir()
    (repository / "vault" / "ledger.sqlite3").write_bytes(b"fixture")
    after = cases.git_binding(repository, task_line="same-task")
    assert before == after


def test_public_summarize_call_is_exact_and_accepts_boolean_success(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import importlib

    runner = importlib.import_module(
        "benchmarks.hosts.run_pass13_opencode_continuity_qualification"
    )
    server = runner._OpenCodeLocalServer(
        binary=tmp_path / "opencode",
        environment={},
        cwd=tmp_path,
        root=tmp_path,
    )
    calls: list[tuple[str, str, object]] = []

    def request(method: str, path: str, payload: object = None) -> bool:
        calls.append((method, path, payload))
        return True

    monkeypatch.setattr(server, "request", request)
    assert server.summarize("session-1") is True
    assert calls == [
        (
            "POST",
            "/session/session-1/summarize",
            {
                "providerID": "deepseek",
                "modelID": "deepseek-v4-flash",
                "auto": False,
            },
        )
    ]


def test_wrapper_receipt_is_validated_after_the_first_host_turn_starts_mcp(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import importlib

    runner = importlib.import_module(
        "benchmarks.hosts.run_pass13_opencode_continuity_qualification"
    )
    case = runner.pass16_continuity_cases.task_case("cold_start")
    receipt_path = tmp_path / "run" / "mcp-wrapper-receipt.json"
    order: list[str] = []

    def prepare(**kwargs: object) -> tuple[dict[str, str], Path]:
        run_root = kwargs["run_root"]
        assert isinstance(run_root, Path)
        run_root.mkdir(parents=True, exist_ok=True)
        (run_root / "deeplaw-closed-mcp").write_text("wrapper\n", encoding="utf-8")
        return {}, receipt_path

    def seed(*args: object, **kwargs: object) -> dict[str, object]:
        return {
            "grant_id": "grant",
            "knowledge_id": "knowledge",
            "revision_id": "revision",
            "seed_boundary": {
                "kind": "seed_checkpoint",
                "owner_enabled": True,
                "read_mcp_write_performed": False,
                "audit_changed": True,
                "audit_head_before": "a" * 64,
                "audit_head_after": "b" * 64,
                "receipt_sha256": "c" * 64,
                "target_sha256": "d" * 64,
            },
        }

    def host_turn(*args: object, **kwargs: object) -> dict[str, object]:
        order.append("turn")
        receipt_path.write_text("{}\n", encoding="utf-8")
        return {
            "returncode": 0,
            "stdout": b'{"sessionID":"session-fixture"}\n',
            "stderr": b"",
            "elapsed_ms": 1,
            "timed_out": False,
            "output_overflow": False,
        }

    def analyze(*args: object, **kwargs: object) -> dict[str, object]:
        current = case["current_checkpoint"]
        final = {
            "summary": "current route",
            "next_step": current["next_action"],
            "preserved_decisions": [current["decision"]],
            "open_gaps": [current["open_gap"]],
        }
        return {
            "safe_read": {
                "call_count": 1,
                "first_call_valid": True,
                "bounded_retry_used": False,
                "safe_read_operations": ["context"],
                "provider_payloads": [
                    {
                        "provider_bytes": 1,
                        "delivery_match": True,
                        "write_performed": False,
                        "gap_count": 0,
                    }
                ],
            },
            "usage": {
                "input_tokens": 1,
                "cached_input_tokens": 0,
                "cache_write_input_tokens": 0,
                "output_tokens": 1,
                "reasoning_output_tokens": 0,
                "total_tokens": 2,
            },
            "thread_id_sha256": "1" * 64,
            "turn_id_sha256": "2" * 64,
            "final_response_sha256": "3" * 64,
            "final_response_bytes": 1,
                "final_value": final,
                "provider_values": [current["decision"], current["next_action"]],
                "provider_texts": [
                    f"{current['decision']} {current['next_action']} {current['marker']}"
                ],
                "sanitized_events": b'{"type":"safe"}\n',
            }

    def validate(receipt: object, **kwargs: object) -> bool:
        assert receipt_path.is_file()
        order.append("receipt")
        return True

    class LocalServer:
        def __init__(self, **kwargs: object) -> None:
            pass

        def start(self) -> None:
            pass

        def stop(self) -> None:
            pass

        def resume(self, session_id: str) -> dict[str, str]:
            return {"id": session_id}

    monkeypatch.setattr(runner, "_prepare_scenario_state", prepare)
    monkeypatch.setattr(runner, "_seed_continuity_fixture", seed)
    monkeypatch.setattr(runner, "_run_opencode_command", host_turn)
    monkeypatch.setattr(runner, "analyze_opencode_events", analyze)
    monkeypatch.setattr(runner, "validate_mcp_receipt", validate)
    monkeypatch.setattr(runner, "_ledger_head", lambda *args, **kwargs: "e" * 64)
    monkeypatch.setattr(runner, "_OpenCodeLocalServer", LocalServer)
    monkeypatch.setattr(
        runner,
        "observe_knowledge_support_tools_list",
        lambda **kwargs: {"tools_list_observed": True},
    )
    monkeypatch.setattr(
        runner,
        "_bind_native_relevant_chars",
        lambda safe_read, outputs, relevant_text: safe_read,
    )

    run, _sanitized, receipts, tool_schema = runner._run_one_scenario(
        run_index=1,
        scenario="cold_start",
        opencode_binary=tmp_path / "opencode",
        deeplaw_executable=tmp_path / "deeplaw",
        environment={},
        run_root=tmp_path / "run",
        forbidden_values=(),
    )
    assert order == ["turn", "receipt"]
    assert run["status"] == "passed"
    assert receipts == [{}]
    assert tool_schema == {"tools_list_observed": True}


def run_git(repository: Path, *args: str) -> str:
    import subprocess

    completed = subprocess.run(
        ["git", *args], cwd=repository, capture_output=True, check=False, text=True
    )
    assert completed.returncode == 0, completed.stderr
    return completed.stdout.strip()


def test_historical_source_revision_is_explicitly_rejected(tmp_path: Path) -> None:
    import importlib

    runner = importlib.import_module(
        "benchmarks.hosts.run_pass13_opencode_continuity_qualification"
    )

    with pytest.raises(runner.QualificationError, match="retired historical"):
        runner._execute_qualification_body(
            candidate_wheel=tmp_path / "candidate.whl",
            deeplaw_executable=tmp_path / "deeplaw",
            output_dir=tmp_path / "output",
            opencode_binary=tmp_path / "opencode",
            dotenv=tmp_path / ".env",
            human_gold_path=tmp_path / "human-gold.json",
            root=tmp_path,
            source_revision_id="historical-source",
        )


def test_missing_external_human_gold_blocks_before_candidate_or_provider_start(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import importlib

    runner = importlib.import_module(
        "benchmarks.hosts.run_pass13_opencode_continuity_qualification"
    )
    called = False

    def prepare_candidate(*args: object, **kwargs: object) -> object:
        nonlocal called
        called = True
        raise AssertionError("candidate preparation must not run before Human Gold")

    monkeypatch.setattr(
        runner.QualificationOrchestrator,
        "prepare_candidate",
        prepare_candidate,
    )
    with pytest.raises(runner.QualificationError, match="frozen external Human Gold"):
        runner._execute_qualification_body(
            candidate_wheel=tmp_path / "candidate.whl",
            deeplaw_executable=tmp_path / "deeplaw",
            output_dir=tmp_path / "output",
            opencode_binary=tmp_path / "opencode",
            dotenv=tmp_path / ".env",
            human_gold_path=tmp_path / "missing-human-gold.json",
            root=tmp_path,
        )
    assert called is False
