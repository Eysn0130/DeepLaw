from __future__ import annotations

import ast
import hashlib
import io
import json
import os
import stat
from pathlib import Path

import pytest

from adapters.claude import deeplaw_hook, install
from deeplaw.agent_context import validate_agent_context

REPOSITORY = Path(__file__).parents[1]
HOOKS_PATH = REPOSITORY / "adapters" / "claude" / "hooks.json"
HOOK_PATH = REPOSITORY / "adapters" / "claude" / "deeplaw_hook.py"
INSTALL_PATH = REPOSITORY / "adapters" / "claude" / "install.py"


def _fake_result() -> dict[str, object]:
    return {
        "schema_version": "deeplaw.purpose-aware-retrieval/v3",
        "query_plan_sha256": "a" * 64,
        "receipt_id": "queryreceipt_" + "b" * 24,
        "purpose": "answer",
        "policy_id": "compiled-first-v1",
        "query_plan": {
            "schema_version": "deeplaw.knowledge-query-plan/v6",
            "selected_statement_count": 1,
            "evidence_selected_count": 0,
            "fallback": {"used": False},
        },
        "metrics": {"duty_coverage": 1.0},
        "statements": [
            {
                "statement_id": "statement_" + "c" * 24,
                "knowledge_id": "knowledge_" + "d" * 24,
                "knowledge_revision_id": "knowledgerev_" + "e" * 24,
                "statement_text": "Bounded governed statement.",
                "statement_type": "factual",
                "support_status": "supported",
                "current_supported": True,
                "freshness": "fresh",
                "authority": "agent_derived",
                "verification": "source_bound",
                "legal_authority": False,
            }
        ],
        "evidence": [],
        "gaps": [{"code": "bounded_gap", "message": "hidden body"}],
    }


def _hook_args(vault: Path, event: str) -> list[str]:
    return [
        "--event",
        event,
        "--vault",
        str(vault),
        "--workspace-identity",
        "workspace-test",
        "--repository-identity",
        "repository-test",
    ]


def _run_hook(
    monkeypatch: pytest.MonkeyPatch,
    payload: dict[str, object],
    args: list[str],
) -> tuple[dict[str, object], dict[str, object]]:
    captured: dict[str, object] = {}
    original_builder = deeplaw_hook.build_agent_context

    def capture_builder(**kwargs: object) -> dict[str, object]:
        envelope = original_builder(**kwargs)  # type: ignore[arg-type]
        captured["envelope"] = envelope
        return envelope

    class FakeService:
        def __init__(self, root: Path) -> None:
            captured["root"] = root

        def query(self, *_args: object, **_kwargs: object) -> dict[str, object]:
            captured["queried"] = True
            return _fake_result()

    monkeypatch.setattr(deeplaw_hook, "build_agent_context", capture_builder)
    monkeypatch.setattr(deeplaw_hook, "PurposeAwareRetrievalService", FakeService)
    event = args[args.index("--event") + 1]
    payload = {**payload, "hook_event_name": event}
    output = io.BytesIO()
    assert (
        deeplaw_hook.main(
            args,
            stdin=io.BytesIO(json.dumps(payload).encode()),
            stdout=output,
        )
        == 0
    )
    parsed = json.loads(output.getvalue())
    return parsed, captured


def test_hooks_template_has_exact_six_command_events_and_is_not_installed() -> None:
    template = json.loads(HOOKS_PATH.read_text(encoding="utf-8"))
    assert set(template) == {"hooks"}
    assert set(template["hooks"]) == set(deeplaw_hook._EVENTS)
    for event, groups in template["hooks"].items():
        command = groups[0]["hooks"][0]
        assert command["type"] == "command"
        assert isinstance(command["command"], str)
        assert command["args"][command["args"].index("--event") + 1] == event
        assert deeplaw_hook.HOOK_MARKER in command["args"]
    assert not (REPOSITORY / ".claude" / "settings.json").exists()


def test_six_events_are_bounded_and_use_one_context_envelope(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    vault = tmp_path / "vault"
    vault.mkdir()
    prompt, captured = _run_hook(
        monkeypatch,
        {"prompt": "Find the bounded answer", "active_files": ["src/app.py"]},
        _hook_args(vault, "UserPromptSubmit"),
    )
    assert prompt["persistence_performed"] is False
    assert prompt["disposition"] == "context_injected"
    assert prompt["capsule_receipt"]["content_redacted"] is True
    additional = json.loads(prompt["hookSpecificOutput"]["additionalContext"])
    assert additional["knowledge_capsule"]["query_plan_version"] == "6"
    assert additional["knowledge_capsule"]["statements"][0]["authority"] == "agent_derived"
    assert "Find the bounded answer" not in json.dumps(prompt)
    assert validate_agent_context(captured["envelope"]) == captured["envelope"]
    assert prompt["envelope_sha256"] == captured["envelope"]["envelope_sha256"]

    compact, _ = _run_hook(
        monkeypatch,
        {"compact_summary": "untrusted compact summary with hidden body"},
        _hook_args(vault, "PostCompact"),
    )
    assert compact["disposition"] == "capsule_requeried_no_context_injection"
    assert compact["untrusted_input"] is True
    assert compact["host_context_injection_supported"] is False
    assert "hookSpecificOutput" not in compact
    assert "untrusted compact summary" not in json.dumps(compact)

    precompact, _ = _run_hook(
        monkeypatch,
        {"trigger": "manual"},
        ["--event", "PreCompact"],
    )
    assert precompact["disposition"] == "task_fingerprint_emitted"
    assert len(precompact["task_fingerprint"]) == 64

    tool, _ = _run_hook(
        monkeypatch,
        {"tool_name": "Read", "tool_response": {"body": "private tool output"}},
        ["--event", "PostToolUse"],
    )
    assert tool["disposition"] == "tool_digest_emitted"
    assert tool["raw_response_emitted"] is False
    assert json.loads(tool["hookSpecificOutput"]["additionalContext"])["sha256"] == tool[
        "sha256"
    ]
    assert "private tool output" not in json.dumps(tool)
    expected = hashlib.sha256(
        json.dumps(
            {"tool_name": "Read", "tool_response": {"body": "private tool output"}},
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode()
    ).hexdigest()
    assert tool["sha256"] == expected

    for event in ("Stop", "SessionEnd"):
        result, _ = _run_hook(monkeypatch, {}, ["--event", event])
        assert result["disposition"] == "backfill_draft_suggested"
        assert result["owner_action_required"] is True
        assert result["promotion_performed"] is False
        if event == "Stop":
            assert result["hookSpecificOutput"]["hookEventName"] == "Stop"
        else:
            assert result["host_context_injection_supported"] is False


def test_hook_noop_rejects_missing_config_oversize_secret_and_paths(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    missing, _ = _run_hook(monkeypatch, {"prompt": "safe"}, ["--event", "UserPromptSubmit"])
    assert missing["disposition"] == "no_op"
    assert "safe" not in json.dumps(missing)

    oversized, _ = _run_hook(
        monkeypatch,
        {"prompt": "x" * (deeplaw_hook.MAX_INPUT_BYTES + 1)},
        ["--event", "UserPromptSubmit"],
    )
    assert oversized["disposition"] == "no_op"

    secret, _ = _run_hook(
        monkeypatch,
        {"prompt": "use sk-live-secret-1234567890"},
        ["--event", "UserPromptSubmit"],
    )
    assert secret["disposition"] == "no_op"
    assert "sk-live-secret" not in json.dumps(secret)

    path, _ = _run_hook(
        monkeypatch,
        {"prompt": "safe"},
        [
            "--event",
            "UserPromptSubmit",
            "--vault",
            str(tmp_path),
            "--workspace-identity",
            "/absolute/workspace",
            "--repository-identity",
            "repo",
        ],
    )
    assert path["disposition"] == "no_op"
    assert str(tmp_path) not in json.dumps(path)


def test_install_uninstall_are_atomic_bounded_idempotent_and_preserve_unknowns(
    tmp_path: Path,
) -> None:
    settings = tmp_path / "settings.json"
    settings.write_text(
        json.dumps(
            {
                "custom": {"keep": True},
                "hooks": {
                    "Stop": [
                        {
                            "matcher": "*",
                            "hooks": [{"type": "command", "command": "echo", "args": ["keep"]}],
                        }
                    ]
                },
            }
        ),
        encoding="utf-8",
    )
    first = install.install_settings(
        settings,
        vault=str(tmp_path / "vault"),
        workspace_identity="workspace-test",
        repository_identity="repository-test",
    )
    assert first["changed"] is True
    if os.name != "nt":
        assert stat.S_IMODE(settings.stat().st_mode) == stat.S_IRUSR | stat.S_IWUSR
        assert first["owner_only"] is True
    else:
        assert first["owner_only"] is False
    initial_bytes = settings.read_bytes()
    second = install.install_settings(
        settings,
        vault=str(tmp_path / "vault"),
        workspace_identity="workspace-test",
        repository_identity="repository-test",
    )
    assert second["idempotent"] is True
    assert settings.read_bytes() == initial_bytes
    merged = json.loads(initial_bytes)
    assert merged["custom"] == {"keep": True}
    assert any(
        hook.get("command") == "echo"
        for group in merged["hooks"]["Stop"]
        for hook in group["hooks"]
    )
    assert sum(
        deeplaw_hook.HOOK_MARKER in hook.get("args", [])
        for groups in merged["hooks"].values()
        for group in groups
        for hook in group["hooks"]
    ) == 6
    assert all(
        Path(hook["args"][0]) == HOOK_PATH.resolve()
        for groups in merged["hooks"].values()
        for group in groups
        for hook in group["hooks"]
        if deeplaw_hook.HOOK_MARKER in hook.get("args", [])
    )

    removed = install.uninstall_settings(settings)
    assert removed["removed_count"] == 6
    after = json.loads(settings.read_text(encoding="utf-8"))
    assert after["custom"] == {"keep": True}
    assert any(
        hook.get("command") == "echo"
        for group in after["hooks"]["Stop"]
        for hook in group["hooks"]
    )
    assert install.uninstall_settings(settings)["idempotent"] is True


def test_install_conflicts_default_path_and_oversize_fail_closed(tmp_path: Path) -> None:
    settings = tmp_path / "settings.json"
    install.install_settings(settings)
    candidate = json.loads(settings.read_text(encoding="utf-8"))
    candidate["hooks"]["Stop"][0]["hooks"][0]["command"] = "evil"
    settings.write_text(json.dumps(candidate), encoding="utf-8")
    with pytest.raises(install.InstallerError):
        install.install_settings(settings)

    with pytest.raises(install.InstallerError):
        install.install_settings(Path.home() / ".claude" / "settings.json")

    with pytest.raises(install.InstallerError):
        install.install_settings(settings, workspace_identity="sk-live-secret-1234567890")

    malformed = json.loads(settings.read_text(encoding="utf-8"))
    malformed["hooks"]["Stop"][0]["hooks"][0] = {
        "type": "shell",
        "args": [install.HOOK_MARKER],
    }
    settings.write_text(json.dumps(malformed), encoding="utf-8")
    with pytest.raises(install.InstallerError):
        install.uninstall_settings(settings)

    settings.write_text(
        "{" + "\"x\":" + "\"a" * install.MAX_SETTINGS_BYTES + "\"}",
        encoding="utf-8",
    )
    with pytest.raises(install.InstallerError):
        install.uninstall_settings(settings)


def test_atomic_write_windows_does_not_require_posix_fchmod(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    settings = tmp_path / "settings.json"
    monkeypatch.delattr(install.os, "fchmod", raising=False)

    install._atomic_write(settings, {"windows": True})

    assert json.loads(settings.read_text(encoding="utf-8")) == {"windows": True}


def test_atomic_write_cleanup_cannot_mask_replace_failure(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    settings = tmp_path / "settings.json"
    descriptor: dict[str, int] = {}
    real_mkstemp = install.tempfile.mkstemp

    def capture_mkstemp(*args: object, **kwargs: object) -> tuple[int, str]:
        fd, temporary = real_mkstemp(*args, **kwargs)
        descriptor["fd"] = fd
        return fd, temporary

    monkeypatch.setattr(install.tempfile, "mkstemp", capture_mkstemp)

    def fail_replace(*_args: object, **_kwargs: object) -> None:
        raise RuntimeError("replace failed")

    monkeypatch.setattr(install.os, "replace", fail_replace)

    def fail_cleanup(self: Path, *args: object, **kwargs: object) -> None:
        raise OSError("cleanup failed")

    monkeypatch.setattr(Path, "unlink", fail_cleanup)

    with pytest.raises(RuntimeError, match="replace failed"):
        install._atomic_write(settings, {"replace": False})
    with pytest.raises(OSError):
        os.fstat(descriptor["fd"])


def test_adapter_sources_have_no_transcript_network_model_or_mutation_surface() -> None:
    for path in (HOOK_PATH, INSTALL_PATH):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        modules = [
            node.module
            for node in ast.walk(tree)
            if isinstance(node, ast.ImportFrom) and node.module
        ]
        assert not any(
            forbidden in module
            for module in modules
            for forbidden in ("knowledge_sink", "subprocess", "requests", "urllib", "socket")
        )
        source = path.read_text(encoding="utf-8")
        assert "transcript_path" not in source
        assert "remember(" not in source
        assert "enable_grant" not in source
        assert "http://" not in source
        assert "https://" not in source
