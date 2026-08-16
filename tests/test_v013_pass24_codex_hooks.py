from __future__ import annotations

import hashlib
import importlib.util
import io
import json
import re
from pathlib import Path
from typing import Any

REPOSITORY = Path(__file__).resolve().parents[1]
PLUGIN_ROOT = REPOSITORY / "plugins" / "deeplaw-knowledge-os"
MANIFEST_PATH = PLUGIN_ROOT / ".codex-plugin" / "plugin.json"
HOOKS_PATH = PLUGIN_ROOT / "hooks" / "hooks.json"

_HOOK_MODULE_SPEC = importlib.util.spec_from_file_location(
    "deeplaw_codex_native_lifecycle",
    PLUGIN_ROOT / "hooks" / "native_lifecycle.py",
)
assert _HOOK_MODULE_SPEC is not None and _HOOK_MODULE_SPEC.loader is not None
native_lifecycle = importlib.util.module_from_spec(_HOOK_MODULE_SPEC)
_HOOK_MODULE_SPEC.loader.exec_module(native_lifecycle)


CANARIES = (
    "transcript-path-canary",
    "raw-prompt-canary",
    "reasoning-secret-canary",
    "auth-secret-canary",
)


def _run_hook(event: str, payload: dict[str, Any]) -> dict[str, Any]:
    output = io.BytesIO()
    result = native_lifecycle.main(
        [event],
        stdin=io.BytesIO(json.dumps(payload).encode("utf-8")),
        stdout=output,
    )
    assert result == 0
    assert len(output.getvalue()) <= native_lifecycle.MAX_OUTPUT_BYTES
    return json.loads(output.getvalue())


def _base_payload(event: str) -> dict[str, Any]:
    return {
        "session_id": "codex-session-opaque-id",
        "cwd": str(REPOSITORY),
        "hook_event_name": event,
        "source": "startup",
        "trigger": "auto",
        "reason": CANARIES[2],
        "transcript_path": CANARIES[0],
        "prompt": CANARIES[1],
        "auth": CANARIES[3],
        "nested_untrusted": {"body": "raw legal/wiki/web text must not cross"},
    }


def _context(result: dict[str, Any]) -> str:
    return str(result["hookSpecificOutput"]["additionalContext"])


def test_manifest_registers_hooks_and_states_owner_exact_hash_trust() -> None:
    manifest = json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))
    hooks = json.loads(HOOKS_PATH.read_text(encoding="utf-8"))

    assert manifest["hooks"] == "./hooks/hooks.json"
    assert manifest["version"] == "0.12.0"
    manifest_text = json.dumps(manifest, ensure_ascii=False).casefold()
    hooks_text = json.dumps(hooks, ensure_ascii=False).casefold()
    assert "owner" in manifest_text and "trust" in manifest_text
    assert "exact" in hooks_text and "trust" in hooks_text
    assert set(hooks["hooks"]) == set(native_lifecycle.SUPPORTED_EVENTS)

    for event, groups in hooks["hooks"].items():
        assert len(groups) == 1
        handlers = groups[0]["hooks"]
        assert len(handlers) == 1
        handler = handlers[0]
        assert handler["type"] == "command"
        assert "${PLUGIN_ROOT}/hooks/native_lifecycle.py" in handler["command"]
        assert handler["command"].endswith(f" {event}")
        if event == "SessionEnd":
            assert handler["timeout"] <= 3
            assert "additionalContextLimit" not in handler
        else:
            assert handler["additionalContextLimit"] == 2048
            assert handler["timeout"] > 0


def test_all_five_hooks_are_bounded_and_redact_untrusted_input() -> None:
    session_digest = hashlib.sha256(b"codex-session-opaque-id").hexdigest()
    for event in native_lifecycle.SUPPORTED_EVENTS:
        result = _run_hook(event, _base_payload(event))
        serialized = json.dumps(result, ensure_ascii=False, sort_keys=True)
        assert all(canary not in serialized for canary in CANARIES)
        assert "transcript_path" not in serialized
        assert str(REPOSITORY) not in serialized

        if event == "SessionEnd":
            assert "hookSpecificOutput" not in result
            assert "additionalContext" not in serialized
            continue

        context = _context(result)
        assert result["hookSpecificOutput"]["hookEventName"] == event
        assert len(context.encode("utf-8")) <= native_lifecycle.MAX_OUTPUT_BYTES
        assert f"session_sha256={session_digest}" in context
        assert re.search(r"repository_sha256=(?:[0-9a-f]{64}|gap:)", context)
        assert re.search(r"worktree_sha256=(?:[0-9a-f]{64}|gap:)", context)
        assert "route_status=unbound" in context
        assert "knowledge_support" in context
        assert "query, context, or explain" in context
        assert (
            'host_route={"host":"codex",'
            f'"session_sha256":"{session_digest}"}}' in context
        )
        assert "task_handle=" not in context


def test_precompact_is_explicitly_read_only_without_checkpoint_grant() -> None:
    result = _run_hook("PreCompact", _base_payload("PreCompact"))
    context = _context(result)

    assert "checkpoint_grant_missing" in context
    assert "checkpoint=not_attempted" in context
    assert "write_performed=false" in context


def test_invalid_utf8_duplicate_nan_and_oversize_input_fail_closed_without_echo() -> None:
    cases = (
        b'{"hook_event_name":"SessionStart","hook_event_name":"UserPromptSubmit"}',
        b'{"hook_event_name":"SessionStart","value":NaN}',
        b"\xff\xfe\xfd",
        b"{" + b"x" * native_lifecycle.MAX_STDIN_BYTES + b"}",
    )
    for raw in cases:
        output = io.BytesIO()
        assert (
            native_lifecycle.main(
                ["SessionStart"], stdin=io.BytesIO(raw), stdout=output
            )
            == 0
        )
        assert len(output.getvalue()) <= native_lifecycle.MAX_OUTPUT_BYTES
        rendered = output.getvalue().decode("utf-8")
        assert "input_invalid" in rendered
        assert all(canary not in rendered for canary in CANARIES)


def test_reason_source_and_trigger_are_reduced_to_safe_route_metadata() -> None:
    payload = _base_payload("SessionStart")
    payload.update({"source": "resume", "trigger": "manual", "reason": CANARIES[2]})
    context = _context(_run_hook("SessionStart", payload))

    assert "source=resume" in context
    assert "trigger=manual" in context
    assert "reason=present" in context
    assert CANARIES[2] not in context


def test_output_bound_holds_for_maximal_bounded_fields() -> None:
    payload = _base_payload("UserPromptSubmit")
    payload["reason"] = "r" * 4096
    payload["prompt"] = "p" * 50_000
    result = _run_hook("UserPromptSubmit", payload)
    assert len(json.dumps(result, ensure_ascii=False).encode("utf-8")) <= 2048
