from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest

from benchmarks.baselines import official_adapter
from benchmarks.hosts import run_codex_continuity_qualification as codex_qualification
from benchmarks.hosts import run_living_wiki_host_harness as living_harness
from benchmarks.hosts import run_semantic_host_harness as semantic_harness
from deeplaw.host_runtime import bind_owner_vault
from deeplaw.knowledge_autonomy import initialize_autonomous_core
from deeplaw.knowledge_store import initialize_knowledge_vault
from deeplaw.util import canonical_json, sha256_bytes

REPOSITORY = Path(__file__).resolve().parents[1]
AMBIENT_NAME = "DEEPLAW_TEST_AMBIENT_SECRET"
PROVIDER_NAME = "TEST_PROVIDER_TOKEN"
AMBIENT_CANARY = "ambient-canary-v013-do-not-persist"
PROVIDER_CANARY = "provider-canary-v013-do-not-persist"


def _command() -> dict[str, Any]:
    # The fake host launches a fake MCP child with ordinary inherited process
    # semantics. Keep sensitive variable names out of argv so _safe_command
    # remains exercised.
    script = (
        "import json, os, subprocess, sys\n"
        "prompt = sys.stdin.read()\n"
        "visible = sorted((k, v) for k, v in os.environ.items() "
        "if k.startswith('DEEPLAW_TEST_') or k.startswith('TEST_PROVIDER_'))\n"
        "child = subprocess.run([sys.executable, '-c', "
        "\"import json, os; print(json.dumps({'env': sorted((k, v) for k, v in "
        "os.environ.items() if k.startswith('DEEPLAW_TEST_') or "
        "k.startswith('TEST_PROVIDER_')), 'path': bool(os.environ.get('PATH')), "
        "'locale': bool(os.environ.get('LC_ALL') or os.environ.get('LANG')), "
        "'temporary': bool(os.environ.get('TMPDIR') or os.environ.get('TMP') or "
        "os.environ.get('TEMP')), 'home': os.environ.get('HOME'), "
        "'xdg_config': os.environ.get('XDG_CONFIG_HOME'), "
        "'codex_home': os.environ.get('CODEX_HOME'), 'cwd': os.getcwd()}, "
        "sort_keys=True))\"], capture_output=True, text=True, "
        "check=True)\n"
        "payload = {'host_env': visible, 'mcp': json.loads(child.stdout), "
        "'host_isolation': {'home': os.environ.get('HOME'), "
        "'xdg_config': os.environ.get('XDG_CONFIG_HOME'), "
        "'codex_home': os.environ.get('CODEX_HOME'), 'cwd': os.getcwd()}, "
        "'prompt': prompt}\n"
        "rendered = json.dumps(payload, sort_keys=True)\n"
        "print(rendered)\n"
        "print(rendered, file=sys.stderr)\n"
    )
    return {
        "schema_version": "deeplaw.real-host-compile-command/v1",
        "argv": [sys.executable, "-c", script],
        "prompt_transport": "stdin_utf8",
        "timeout_seconds": 30,
        "max_output_bytes": 1024 * 1024,
    }


def _gold() -> dict[str, Any]:
    return json.loads(
        (REPOSITORY / "benchmarks/semantic/semantic-gold-candidate-v1.json").read_text(
            encoding="utf-8"
        )
    )


def _corpus_v1(gold: dict[str, Any]) -> dict[str, Any]:
    return {
        "schema_version": "deeplaw.semantic-host-corpus/v1",
        "gold_id": gold["gold_id"],
        "fixture_manifest_sha256": gold["fixture_manifest_sha256"],
        "sources": [
            {
                "source_key": source["source_key"],
                "source_revision_id": f"sourcerev_{index:024x}",
            }
            for index, source in enumerate(gold["sources"], start=1)
        ],
    }


def _corpus_v2(gold: dict[str, Any]) -> dict[str, Any]:
    atlas_key = "sourcekey_" + "a" * 24
    sources = []
    for index, source in enumerate(gold["sources"], start=1):
        canonical_source_key = (
            atlas_key
            if source["source_key"] in {"update-v1", "update-v2"}
            else f"sourcekey_{index:024x}"
        )
        sources.append(
            {
                "source_key": source["source_key"],
                "canonical_source_key": canonical_source_key,
                "source_id": f"source_{index:024x}",
                "source_revision_id": f"sourcerev_{index:024x}",
                "phase": "successor" if source["source_key"] == "update-v2" else "baseline",
                "initial_lifecycle_status": (
                    "pending" if source["source_key"] == "update-v2" else "active"
                ),
                "review_manifest_sha256": f"{index:064x}",
                "sensitivity": source["sensitivity"],
            }
        )
    return {
        "schema_version": "deeplaw.semantic-host-corpus/v2",
        "corpus_id": "semanticcorpus_0123456789abcdef01234567",
        "gold_id": gold["gold_id"],
        "fixture_manifest_sha256": gold["fixture_manifest_sha256"],
        "vault_id": "vault_0123456789abcdef01234567",
        "snapshot_sha256": "a" * 64,
        "grant_id": "grant_0123456789abcdef01234567",
        "sources": sources,
        "transitions": [
            {
                "operation": "activate_successor",
                "predecessor_source_key": "update-v1",
                "successor_source_key": "update-v2",
            },
            {"operation": "withdraw_source", "source_key": "retention-a"},
        ],
    }


def _assert_no_canary(value: Any) -> None:
    rendered = repr(value)
    assert AMBIENT_CANARY not in rendered
    assert PROVIDER_CANARY not in rendered


def _assert_process_environment(environment: dict[str, str]) -> None:
    _assert_no_canary(environment)
    assert environment["PATH"] == os.environ.get("PATH", os.defpath)
    for name in ("LANG", "LC_ALL", "LC_CTYPE", "TMPDIR", "TMP", "TEMP"):
        assert environment.get(name)
    for name in ("COMSPEC", "PATHEXT", "SYSTEMROOT", "WINDIR"):
        if name in os.environ:
            assert environment[name] == os.environ[name]


def test_legacy_full_environment_copy_reproduces_ambient_canary_leak(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv(AMBIENT_NAME, AMBIENT_CANARY)

    # This is the regression reproducer: all three old harness paths used this
    # exact operation before the closed allowlist was introduced.
    inherited = os.environ.copy()

    assert inherited[AMBIENT_NAME] == AMBIENT_CANARY


def test_closed_environment_defaults_to_no_ambient_or_provider_secret(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv(AMBIENT_NAME, AMBIENT_CANARY)
    monkeypatch.setenv(PROVIDER_NAME, PROVIDER_CANARY)

    environment = living_harness._host_environment(
        fixed={"DEEPLAW_TEST_FIXED": "1"}
    )

    assert living_harness._INHERITED_ENVIRONMENT == (
        official_adapter._DEFAULT_INHERITED_ENVIRONMENT
    )
    _assert_process_environment(environment)
    assert AMBIENT_NAME not in environment
    assert PROVIDER_NAME not in environment
    assert environment["DEEPLAW_TEST_FIXED"] == "1"

    # There is deliberately no environment-secret opt-in. A Host secret in
    # this environment would also be inherited by its MCP subprocess, so real
    # provider execution remains blocked until that exact host proves a
    # separate authentication boundary.
    assert "provider_auth_env" not in living_harness.execute.__annotations__


def test_fake_mcp_child_cannot_see_ambient_or_provider_secret(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv(AMBIENT_NAME, AMBIENT_CANARY)
    monkeypatch.setenv(PROVIDER_NAME, PROVIDER_CANARY)
    environment = living_harness._host_environment(fixed={})

    exit_code, stdout, stderr, failure = living_harness._run_bounded(
        living_harness._safe_command(_command()),
        prompt=b"bounded fake-host prompt",
        environment=environment,
        timeout_seconds=30,
        max_output_bytes=1024 * 1024,
    )

    assert (exit_code, failure) == (0, None)
    payload = json.loads(stdout)
    assert payload["host_env"] == []
    assert payload["mcp"]["env"] == []
    assert payload["mcp"]["path"] is True
    assert payload["mcp"]["locale"] is True
    assert payload["mcp"]["temporary"] is True
    original_home = Path.home()
    for key in ("home", "xdg_config", "codex_home", "cwd"):
        host_path = Path(payload["host_isolation"][key])
        child_path = Path(payload["mcp"][key])
        assert host_path == child_path
        assert host_path != original_home
        assert REPOSITORY not in host_path.parents
        # The wrapper removes the entire isolated HOME/XDG/cwd tree after the
        # host and its MCP child exit.
        assert not host_path.exists()
    _assert_no_canary((stdout, stderr, payload))


class _FakeStore:
    def __init__(self, *_args: Any, **_kwargs: Any) -> None:
        self.vault_id = "vault_0123456789abcdef01234567"

    def __enter__(self) -> _FakeStore:
        return self

    def __exit__(self, *_args: Any) -> None:
        return None

    def verify(self) -> dict[str, bool]:
        return {"valid": False}


def test_living_wiki_harness_does_not_forward_ambient_inputs(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv(AMBIENT_NAME, AMBIENT_CANARY)
    monkeypatch.setenv(PROVIDER_NAME, PROVIDER_CANARY)
    vault = tmp_path / "vault"
    vault.mkdir()
    captured: dict[str, Any] = {}
    real_run_bounded = living_harness._run_bounded

    def capture_run(
        argv: list[str],
        *,
        prompt: bytes,
        environment: dict[str, str],
        timeout_seconds: int,
        max_output_bytes: int,
    ) -> tuple[int, bytes, bytes, str | None]:
        captured.update(argv=list(argv), prompt=prompt, environment=dict(environment))
        return real_run_bounded(
            argv,
            prompt=prompt,
            environment=environment,
            timeout_seconds=timeout_seconds,
            max_output_bytes=max_output_bytes,
        )

    monkeypatch.setattr(living_harness, "_run_bounded", capture_run)
    monkeypatch.setattr(living_harness, "_source_runs", lambda *_args: [])
    report = living_harness.execute(
        host="codex",
        host_version="test",
        model_identity="test-model",
        source_revision_id="sourcerev_0123456789abcdef01234567",
        network_policy="offline",
        vault=vault,
        command=_command(),
    )
    report_path = tmp_path / "living-report.json"
    living_harness._write_report(report, report_path)

    _assert_process_environment(captured["environment"])
    _assert_no_canary(captured)
    _assert_no_canary(report)
    _assert_no_canary(report_path.read_bytes())
    assert captured["argv"] == _command()["argv"]
    assert captured["prompt"]


def test_semantic_harness_does_not_forward_ambient_inputs(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv(AMBIENT_NAME, AMBIENT_CANARY)
    monkeypatch.setenv(PROVIDER_NAME, PROVIDER_CANARY)
    gold = _gold()
    corpus = _corpus_v1(gold)
    vault = tmp_path / "vault"
    vault.mkdir()
    captured: dict[str, Any] = {}
    real_run_bounded = semantic_harness._run_bounded

    def capture_run(
        argv: list[str],
        *,
        prompt: bytes,
        environment: dict[str, str],
        timeout_seconds: int,
        max_output_bytes: int,
    ) -> tuple[int, bytes, bytes, str | None]:
        captured.update(argv=list(argv), prompt=prompt, environment=dict(environment))
        return real_run_bounded(
            argv,
            prompt=prompt,
            environment=environment,
            timeout_seconds=timeout_seconds,
            max_output_bytes=max_output_bytes,
        )

    monkeypatch.setattr(semantic_harness, "_run_bounded", capture_run)
    monkeypatch.setattr(semantic_harness, "_run_rows", lambda *_args: [])
    monkeypatch.setattr(semantic_harness, "AutonomousKnowledgeStore", _FakeStore)
    report = semantic_harness.execute(
        host="codex",
        host_version="test",
        model_identity="test-model",
        network_policy="offline",
        grant_id="grant_0123456789abcdef01234567",
        gold=gold,
        corpus=corpus,
        vault=vault,
        command=_command(),
    )
    report_path = tmp_path / "semantic-report.json"
    semantic_harness._write_report(report, report_path)

    _assert_process_environment(captured["environment"])
    _assert_no_canary(captured)
    _assert_no_canary(report)
    _assert_no_canary(report_path.read_bytes())
    assert captured["argv"] == _command()["argv"]
    assert captured["prompt"]


def test_phased_semantic_harness_uses_the_same_closed_environment(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv(AMBIENT_NAME, AMBIENT_CANARY)
    monkeypatch.setenv(PROVIDER_NAME, PROVIDER_CANARY)
    gold = _gold()
    corpus = _corpus_v2(gold)
    vault = tmp_path / "vault"
    vault.mkdir()
    captured: dict[str, Any] = {}

    class _Connection:
        def execute(self, *_args: Any) -> list[dict[str, Any]]:
            return [
                {
                    "source_revision_id": source["source_revision_id"],
                    "source_key": source["canonical_source_key"],
                    "source_id": source["source_id"],
                    "status": source["initial_lifecycle_status"],
                }
                for source in corpus["sources"]
            ]

    class _PhasedStore(_FakeStore):
        def __init__(self, *_args: Any, **_kwargs: Any) -> None:
            super().__init__()
            self.vault_id = corpus["vault_id"]
            self.connection = _Connection()

    def capture_phase(**kwargs: Any) -> dict[str, Any]:
        captured.update(
            argv=list(kwargs["argv"]),
            prompt=kwargs["prompt"],
            environment=dict(kwargs["environment"]),
        )
        return {
            "phase": kwargs["phase"],
            "prompt_sha256": "0" * 64,
            "exit_code": 1,
            "stdout_sha256": "0" * 64,
            "stdout_bytes": 0,
            "stderr_sha256": "0" * 64,
            "stderr_bytes": 0,
            "token_usage": {
                "status": "unreported",
                "input_tokens": None,
                "output_tokens": None,
                "total_tokens": None,
            },
            "elapsed_ms": 0,
            "failure_class": "test_failure",
        }

    monkeypatch.setattr(semantic_harness, "_validate_inputs", lambda **_kwargs: None)
    monkeypatch.setattr(semantic_harness, "_run_rows", lambda *_args: [])
    monkeypatch.setattr(semantic_harness, "AutonomousKnowledgeStore", _PhasedStore)
    monkeypatch.setattr(semantic_harness, "_phase_execution", capture_phase)
    report = semantic_harness.execute_phased(
        host="codex",
        host_version="test",
        model_identity="test-model",
        network_policy="offline",
        grant_id=corpus["grant_id"],
        gold=gold,
        corpus=corpus,
        vault=vault,
        command=_command(),
        deeplaw_command=_command(),
        baseline_query_vault=tmp_path / "baseline-query-vault",
    )
    report_path = tmp_path / "semantic-phased-report.json"
    semantic_harness._write_report(report, report_path)

    _assert_process_environment(captured["environment"])
    _assert_no_canary(captured)
    _assert_no_canary(report)
    _assert_no_canary(report_path.read_bytes())
    assert captured["argv"] == _command()["argv"]
    assert captured["prompt"]


def test_codex_qualification_shim_delegates_to_production_closed_launcher(
    tmp_path: Path,
) -> None:
    output_dir = tmp_path / "qualification"
    output_dir.mkdir()
    if os.name == "nt":
        runtime_executable = Path(sys.executable).with_name("deeplaw.exe")
        assert runtime_executable.is_file(), (
            "native Windows test requires installed Scripts/deeplaw.exe"
        )
        wrapper, _ = codex_qualification._prepare_runtime(
            output_dir=output_dir,
            deeplaw_executable=runtime_executable,
        )
    else:
        runtime = output_dir / "runtime"
        runtime_bin = runtime / "bin"
        runtime_bin.mkdir(parents=True)
        fake_deeplaw = runtime_bin / "deeplaw"
        fake_deeplaw.write_text(
            f"#!{Path(sys.executable).resolve()}\n"
            "import json, os, sys\n"
            "blocked = ('CODEX_HOME', 'OPENAI_API_KEY', 'DEEPSEEK_API_KEY', "
            "'DEEPLAW_QUALIFICATION_SECRET_CANARY', "
            "'DEEPLAW_QUALIFICATION_PROVIDER_CANARY', "
            "'DEEPLAW_CREDENTIAL_PATH_CANARY')\n"
            "print(json.dumps({'argv': sys.argv, "
            "'blocked_names_present': sorted(name for name in blocked if name in os.environ)}, "
            "sort_keys=True))\n",
            encoding="utf-8",
        )
        fake_deeplaw.chmod(0o700)
        wrapper = output_dir / "deeplaw-closed-mcp"
        wrapper.write_text(
            codex_qualification._wrapper_source(Path(sys.executable)),
            encoding="utf-8",
        )
        wrapper.chmod(0o700)
    wrapper_source = wrapper.read_text(encoding="utf-8")
    assert 'if os.name == "nt":' in wrapper_source
    assert "subprocess.run(" in wrapper_source
    assert "shell=False" in wrapper_source
    assert "raise SystemExit(completed.returncode)" in wrapper_source
    assert (
        "os.execve(sys.executable, [sys.executable, *child_argv], launch.environment)"
        in wrapper_source
    )
    vault = output_dir / "vault"
    initialize_knowledge_vault(vault, name="qualification-shim", scope="project")
    initialize_autonomous_core(vault)
    binding = bind_owner_vault(vault, owner_home=output_dir / "mcp-owner")
    vault_id = binding["vault_id"]
    environment = {
        **os.environ,
        "DEEPLAW_QUALIFICATION_SECRET_CANARY": "secret-canary",
        "DEEPLAW_QUALIFICATION_PROVIDER_CANARY": "provider-canary",
        "DEEPLAW_CREDENTIAL_PATH_CANARY": "credential-path-canary",
        "CODEX_HOME": "credential-home-canary",
        "OPENAI_API_KEY": "provider-auth-canary",
        "DEEPSEEK_API_KEY": "deepseek-auth-canary",
    }

    completed = subprocess.run(
        [
            sys.executable,
            str(wrapper),
            "knowledge",
            "mcp",
            "--closed-environment",
            "--stdio",
            "--expected-vault-id",
            vault_id,
        ],
        cwd=output_dir,
        env=environment,
        check=False,
        capture_output=True,
        input="",
        text=True,
        timeout=30,
    )
    assert completed.returncode == 0, completed.stderr

    receipt = json.loads(
        (output_dir / "mcp-environment-receipt.json").read_text(encoding="utf-8")
    )
    expected_child_argv = [
        (
            "runtime/Scripts/deeplaw.exe"
            if os.name == "nt"
            else "runtime/bin/deeplaw"
        ),
        "knowledge",
        "mcp",
        "--closed-environment",
        "--stdio",
        "--expected-vault-id",
        vault_id,
    ]
    if os.name == "nt":
        assert completed.stdout == ""
    else:
        child = json.loads(completed.stdout)
        assert child == {
            "argv": expected_child_argv,
            "blocked_names_present": [],
        }
    assert receipt == {
        "schema_version": "deeplaw.closed-mcp-environment-receipt/v1",
        "closed": True,
        "home_isolated": True,
        "blocked_names_present": [],
        "environment_names": receipt["environment_names"],
        "child_argv": expected_child_argv,
    }
    assert {"HOME", "PATH", "XDG_CONFIG_HOME"}.issubset(
        receipt["environment_names"]
    )
    assert set(receipt["environment_names"]).issubset(
        codex_qualification._ALLOWED_MCP_ENVIRONMENT_NAMES
    )


def test_codex_qualification_binds_windows_installed_wheel_layout_and_exe_argv(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    output_dir = tmp_path / "qualification"
    output_dir.mkdir()
    scripts = tmp_path / "runtime" / "Scripts"
    scripts.mkdir(parents=True)
    runtime_python = scripts / "python.exe"
    runtime_executable = scripts / "deeplaw.exe"
    runtime_python.write_bytes(b"python")
    runtime_executable.write_bytes(b"deeplaw")

    mismatched_scripts = tmp_path / "mismatched-runtime" / "Scripts"
    mismatched_scripts.mkdir(parents=True)
    mismatched_executable = mismatched_scripts / "deeplaw.exe"
    mismatched_executable.write_bytes(b"deeplaw")
    (mismatched_scripts / "python").write_bytes(b"python")
    with pytest.raises(ValueError, match="adjacent Python interpreter"):
        codex_qualification._prepare_runtime(
            output_dir=tmp_path / "mismatched-output",
            deeplaw_executable=mismatched_executable,
        )

    mixed_root = tmp_path / "mixed-runtime"
    mixed_scripts = mixed_root / "Scripts"
    mixed_bin = mixed_root / "bin"
    mixed_scripts.mkdir(parents=True)
    mixed_bin.mkdir()
    mixed_executable = mixed_scripts / "deeplaw.exe"
    mixed_executable.write_bytes(b"deeplaw")
    (mixed_scripts / "python.exe").write_bytes(b"python")
    (mixed_bin / "python").write_bytes(b"python")
    with pytest.raises(ValueError, match="mixed platform layouts"):
        codex_qualification._prepare_runtime(
            output_dir=tmp_path / "mixed-output",
            deeplaw_executable=mixed_executable,
        )

    wrapper, _ = codex_qualification._prepare_runtime(
        output_dir=output_dir,
        deeplaw_executable=runtime_executable,
    )

    assert wrapper == output_dir / "deeplaw-closed-mcp"
    source = wrapper.read_text(encoding="utf-8")
    assert "child_argv = ['runtime/Scripts/deeplaw.exe', *arguments]" in source
    assert "[child_argv[0], *arguments]," in source
    assert "[sys.executable, *child_argv]" in source

    receipt = {
        "schema_version": "deeplaw.closed-mcp-environment-receipt/v1",
        "closed": True,
        "home_isolated": True,
        "blocked_names_present": [],
        "environment_names": ["HOME", "PATH", "XDG_CONFIG_HOME"],
        "child_argv": [
            "runtime/Scripts/deeplaw.exe",
            "knowledge",
            "mcp",
            "--closed-environment",
            "--stdio",
            "--expected-vault-id",
            "vault_" + "0" * 24,
        ],
    }
    receipt_path = output_dir / "mcp-environment-receipt.json"
    receipt_path.write_text(json.dumps(receipt), encoding="utf-8")
    monkeypatch.setattr(codex_qualification.os, "name", "nt")
    assert codex_qualification._environment_receipt(receipt_path) == receipt

    receipt["child_argv"][0] = "runtime/bin/deeplaw"
    receipt_path.write_text(json.dumps(receipt), encoding="utf-8")
    assert codex_qualification._environment_receipt(receipt_path) is None


def test_codex_qualification_fixture_and_event_receipts_are_bounded(
    tmp_path: Path,
) -> None:
    fixture_path = (
        REPOSITORY
        / "benchmarks/v013/qualification/candidate/continuity-task-suite-v1.json"
    )
    fixture = codex_qualification._candidate_fixture(fixture_path)
    vault = tmp_path / "vault"
    seeded = codex_qualification._seed_vault(vault, fixture)
    preflight = codex_qualification._preflight(vault, fixture, seeded)

    assert preflight["status"] == "passed"
    assert preflight["provider_bytes"] <= 65_536
    assert preflight["wrong_state_admission"] == 0
    assert preflight["stale_state_admitted"] is False
    assert preflight["write_performed"] is False

    final = {
        "summary": "The candidate remains release closed.",
        "next_step": "Continue the owner-review preparation.",
        "preserved_decisions": ["Keep package 0.12.0."],
        "open_gaps": ["Qualification evidence is incomplete."],
        "artifact_refs": ["commit:539007a"],
    }
    provider_capsule = preflight["provider_capsule"]
    events = [
        {"type": "thread.started", "thread_id": "thread_fixture"},
        {
            "type": "item.started",
            "item": {
                "id": "item_tool",
                "type": "mcp_tool_call",
                "tool": "deeplaw.knowledge_support",
                "status": "in_progress",
                "arguments": {"operation": "context"},
                "result": None,
            },
        },
        {
            "type": "item.completed",
            "item": {
                "id": "item_tool",
                "type": "mcp_tool_call",
                "tool": "deeplaw.knowledge_support",
                "status": "completed",
                "arguments": {"operation": "context"},
                "result": {
                    "schema_version": "deeplaw.knowledge-support-output/v6",
                    "operation": "context",
                    "result": provider_capsule,
                },
            },
        },
        {
            "type": "item.completed",
            "item": {
                "id": "item_message",
                "type": "agent_message",
                "text": canonical_json(final),
            },
        },
        {
            "type": "turn.completed",
            "usage": {
                "input_tokens": 12,
                "cached_input_tokens": 4,
                "output_tokens": 3,
            },
        },
    ]
    sanitized, tool_calls, parsed_final, provider_output = (
        codex_qualification._sanitized_events(events)
    )
    usage, completed_turns = codex_qualification._usage(events)

    assert sanitized
    assert tool_calls == [
        {
            "tool": "deeplaw.knowledge_support",
            "status": "completed",
            "arguments_sha256": sha256_bytes(
                canonical_json({"operation": "context"}).encode()
            ),
            "result_sha256": sha256_bytes(provider_output),
            "result_bytes": len(provider_output),
        }
    ]
    assert parsed_final == final
    assert codex_qualification._provider_capsule_from_events(events) == provider_capsule
    assert usage == {
        "status": "provider_reported",
        "input_tokens": 12,
        "cached_input_tokens": 4,
        "output_tokens": 3,
        "total_tokens": 15,
    }
    assert completed_turns == 1
    assert codex_qualification._confirmed_login_status(
        subprocess.CompletedProcess(
            ["codex", "login", "status"],
            0,
            stdout="",
            stderr="Logged in using ChatGPT\n",
        )
    )
    assert not codex_qualification._confirmed_login_status(
        subprocess.CompletedProcess(
            ["codex", "login", "status"],
            0,
            stdout="caller supplied Logged in using ChatGPT",
            stderr="",
        )
    )
