from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

from benchmarks.hosts import pass16_continuity_cases
from benchmarks.hosts import run_pass13_codex_continuity_qualification as runner


def test_codex_uses_the_frozen_three_case_matrix_and_neutral_prompts() -> None:
    loaded = pass16_continuity_cases.load_cases()
    assert loaded["model_outputs_seen_before_freeze"] is False
    assert tuple(row["scenario"] for row in loaded["task_cases"]) == runner.SCENARIOS
    assert runner.SCENARIOS == ("cold_start", "resume_fork", "compaction_forget")
    for scenario in runner.SCENARIOS:
        prompt = runner.SCENARIO_TASKS[scenario]
        assert "gold" not in prompt.casefold()
        assert "score" not in prompt.casefold()
        assert prompt == pass16_continuity_cases.candidate_prompt(
            pass16_continuity_cases.task_case(scenario)
        )


def test_codex_seeds_only_frozen_pass16_markers() -> None:
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
        assert "PASS13-" not in json.dumps(case)


def test_codex_git_binding_uses_real_oid_and_is_path_free(tmp_path: Path) -> None:
    repository, concurrent, primary, alternate = runner._create_git_task_repository(
        tmp_path,
        task_line="continuity_cold_new_v1",
    )
    assert repository.is_dir()
    assert concurrent.is_dir()
    assert re.fullmatch(r"[0-9a-f]{40}", primary["base_revision"])
    assert primary["base_revision"] == alternate["base_revision"]
    assert primary["worktree_sha256"] != alternate["worktree_sha256"]
    assert pass16_continuity_cases.binding_sha256(primary) == primary["binding_sha256"]
    assert str(tmp_path) not in json.dumps(primary, sort_keys=True)


def test_codex_binding_changes_when_real_worktree_state_changes(tmp_path: Path) -> None:
    repository, _concurrent, before, _alternate = runner._create_git_task_repository(
        tmp_path,
        task_line="continuity_cold_new_v1",
    )
    dirty_file = repository / "TASK.md"
    dirty_file.write_text("Pass 16 changed task state.\n", encoding="utf-8")
    after = pass16_continuity_cases.git_binding(repository, task_line="pass16-primary")
    assert before["dirty_state_sha256"] != after["dirty_state_sha256"]
    assert before["binding_sha256"] != after["binding_sha256"]


def test_codex_runner_owned_vault_does_not_change_the_frozen_binding(
    tmp_path: Path,
) -> None:
    repository, _concurrent, before, _alternate = runner._create_git_task_repository(
        tmp_path,
        task_line="continuity_cold_new_v1",
    )
    (repository / "vault").mkdir()
    (repository / "vault" / "ledger.sqlite3").write_bytes(b"fixture")
    after = pass16_continuity_cases.git_binding(
        repository, task_line="continuity_cold_new_v1"
    )
    assert before == after


def test_make_binding_is_real_git_compatibility_seam() -> None:
    binding = runner._make_binding("cold_start")
    assert re.fullmatch(r"[0-9a-f]{40}", binding["base_revision"])
    assert pass16_continuity_cases.binding_sha256(binding) == binding["binding_sha256"]
    assert "PASS13-" not in json.dumps(binding)


def test_prompt_and_checkpoint_body_bind_frozen_case_without_old_markers(
    tmp_path: Path,
) -> None:
    scenario = "cold_start"
    case = pass16_continuity_cases.task_case(scenario)
    repository, _concurrent, binding, _alternate = runner._create_git_task_repository(
        tmp_path,
        task_line="continuity_cold_new_v1",
    )
    prompt = runner._prompt(scenario, binding, case=case)
    assert case["task_prompt"] in prompt
    assert "PASS13-" not in prompt
    current = case["current_checkpoint"]
    body = runner._checkpoint_body(
        scenario,
        decision=current["decision"],
        next_action=current["next_action"],
        verified=current["verified_fact"],
        gap=current["open_gap"],
        artifact="pass16-current",
        marker=current["marker"],
        route="current",
        binding=binding,
    )
    assert current["marker"] in body
    assert binding["binding_sha256"] in body
    assert "PASS13-" not in body
    assert repository.is_dir()


def test_compaction_checkpoint_seeds_the_frozen_forget_challenge(tmp_path: Path) -> None:
    case = pass16_continuity_cases.task_case("compaction_forget")
    _repository, _concurrent, binding, _alternate = runner._create_git_task_repository(
        tmp_path,
        task_line=str(case["task_case"]),
    )
    current = case["current_checkpoint"]
    forgotten = case["post_forget_requirement"]["forgotten_marker"]
    body = runner._checkpoint_body(
        "compaction_forget",
        decision=current["decision"],
        next_action=current["next_action"],
        verified=current["verified_fact"],
        gap=current["open_gap"],
        artifact="pass16-current",
        marker=current["marker"],
        forget_marker=forgotten,
        route="current",
        binding=binding,
    )
    assert f"FORGET_MARKER: {forgotten}" in body


def test_git_task_setup_never_invokes_codex_or_provider(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    calls: list[list[str]] = []
    original_run = runner.subprocess.run

    def capture(command: object, *args: object, **kwargs: object) -> object:
        if isinstance(command, (list, tuple)):
            calls.append([str(item) for item in command])
        return original_run(command, *args, **kwargs)

    monkeypatch.setattr(runner.subprocess, "run", capture)
    runner._create_git_task_repository(tmp_path, task_line="continuity_cold_new_v1")
    assert calls
    assert all(command and command[0] == "git" for command in calls)
