from __future__ import annotations

import hashlib
import json
import shutil
import subprocess
import textwrap
from pathlib import Path

import pytest

REPOSITORY = Path(__file__).resolve().parents[1]
PLUGIN = REPOSITORY / "adapters" / "opencode"
SOURCE_PATH = PLUGIN / "plugins" / "deeplaw-native.ts"
MANIFEST_PATH = PLUGIN / "manifest.json"
README_PATH = PLUGIN / "README.md"
BUN = shutil.which("bun")


def _bun_probe(script: str) -> dict[str, object]:
    if BUN is None:
        pytest.skip("local Bun runtime is unavailable")
    result = subprocess.run(
        [BUN, "-e", textwrap.dedent(script)],
        cwd=REPOSITORY,
        check=True,
        capture_output=True,
        text=True,
        timeout=15,
    )
    return json.loads(result.stdout)


def test_manifest_and_readme_freeze_exact_candidate_identity() -> None:
    manifest = json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))
    native = manifest["native_plugin"]
    assert native == {
        "version": "1.18.16",
        "source_commit": "a3647eb025c7615159d417dcc49fc39fdaeba65b",
        "config_selector": "deepseek/deepseek-v4-flash",
        "expected_response_model_id": "deepseek-v4-flash",
        "entrypoint": "plugins/deeplaw-native.ts",
        "status": "candidate_requires_owner_review",
    }
    readme = README_PATH.read_text(encoding="utf-8")
    for required in (
        "1.18.16",
        "a3647eb025c7615159d417dcc49fc39fdaeba65b",
        "deepseek/deepseek-v4-flash",
        "knowledge_support",
        "checkpoint_grant_missing",
        "resolve-host-session",
        "candidate seam",
    ):
        assert required in readme


def test_plugin_source_is_thin_and_does_not_touch_prompt_parts_or_bind() -> None:
    source = SOURCE_PATH.read_text(encoding="utf-8")
    for seam in (
        '"chat.message"',
        "event:",
        '"experimental.chat.system.transform"',
        '"experimental.session.compacting"',
        "session.created",
        "session.updated",
        "session.compacted",
        "Bun",
        "knowledge_support",
        "checkpoint_grant_missing",
    ):
        assert seam in source
    assert "output.parts" not in source
    assert "bind-host-session" not in source
    assert "routeCache" not in source
    assert "console.log" not in source
    assert "DEEPSEEK_API_KEY" not in source


def test_bun_helpers_cover_parent_identity_and_all_native_seams() -> None:
    result = _bun_probe(
        """
        import { observeEvent, createOpenCodeHooks, childEnvironmentPolicy } from
          './adapters/opencode/plugins/deeplaw-native.ts'

        const withParent = observeEvent({
          type: 'session.created',
          info: { id: 'child-session', parentID: 'parent-session', title: 'fork title' },
        })
        const withoutParent = observeEvent({
          type: 'session.created',
          info: { id: 'child-session', title: 'same order as another session' },
        })
        const unsupported = observeEvent({ type: 'session.deleted', info: { id: 'secret' } })
        const hooks = await createOpenCodeHooks('/definitely/missing/deeplaw-worktree')
        const system = { system: [] }
        const compact = { context: [] }
        await hooks['chat.message'](
          { sessionID: 'chat-session', parts: [{ text: 'prompt-canary' }] },
        )
        await hooks['experimental.chat.system.transform'](
          { sessionID: 'chat-session', prompt: 'raw-prompt-canary' },
          system,
        )
        await hooks['experimental.session.compacting'](
          { sessionID: 'chat-session' },
          compact,
        )
        console.log(JSON.stringify({
          withParent,
          withoutParent,
          unsupported,
          hookKeys: Object.keys(hooks).sort(),
          system,
          compact,
          env: childEnvironmentPolicy(),
        }))
        """
    )
    with_parent = result["withParent"]
    without_parent = result["withoutParent"]
    unsupported = result["unsupported"]
    assert isinstance(with_parent, dict)
    assert isinstance(without_parent, dict)
    assert isinstance(unsupported, dict)
    assert with_parent["status"] == "observed"
    assert with_parent["parent_session_sha256"] == hashlib.sha256(
        b"parent-session"
    ).hexdigest()
    assert without_parent["parent_session_sha256"] is None
    assert without_parent["parent_gap"] == "parent_absent"
    assert unsupported["status"] == "gap"
    assert unsupported["gap"] == "event_unknown"
    assert result["hookKeys"] == [
        "chat.message",
        "event",
        "experimental.chat.system.transform",
        "experimental.session.compacting",
    ]

    serialized = json.dumps(result, ensure_ascii=False, sort_keys=True)
    assert "prompt-canary" not in serialized
    assert "raw-prompt-canary" not in serialized
    assert "fork title" not in serialized
    assert "same order" not in serialized
    assert "secret" not in serialized
    system = result["system"]
    compact = result["compact"]
    assert isinstance(system, dict) and isinstance(compact, dict)
    assert len(json.dumps(system, ensure_ascii=False).encode("utf-8")) <= 2048
    assert len(json.dumps(compact, ensure_ascii=False).encode("utf-8")) <= 2048
    assert "knowledge_support" in json.dumps(system)
    assert "checkpoint_grant_missing" in json.dumps(compact)
    assert "write_performed=false" in json.dumps(compact)

    env = result["env"]
    assert isinstance(env, dict)
    assert set(env) <= {"PATH", "LANG", "LC_ALL"}
    assert not any("DEEPSEEK" in key.upper() for key in env)


def test_bun_route_resolution_uses_argv_no_shell_and_only_read_only_result() -> None:
    result = _bun_probe(
        """
        import { createHash } from 'node:crypto'
        import { resolveHostSession } from
          './adapters/opencode/plugins/deeplaw-native.ts'

        const stream = (text) => new ReadableStream({
          start(controller) {
            controller.enqueue(new TextEncoder().encode(text))
            controller.close()
          },
        })
        const session = 'a'.repeat(64)
        const taskHandle = 'taskh_route_candidate'
        const taskHandleSha = createHash('sha256').update(taskHandle).digest('hex')
        let observed
        const fakeSpawn = (argv, options) => {
          observed = { argv, options }
          return {
            stdout: stream(JSON.stringify({
              schema_version: 'deeplaw.host-session-route-result/v1',
              operation: 'resolve',
              status: 'exact',
              host: 'opencode',
              session_sha256: session,
              task_handle: taskHandle,
              task_handle_sha256: taskHandleSha,
              repository_sha256: 'c'.repeat(64),
              worktree_sha256: 'd'.repeat(64),
              binding_sha256: 'e'.repeat(64),
              write_performed: false,
              transcript_copied: false,
            })),
            stderr: stream('ignored-error-output'),
            exited: Promise.resolve(0),
            kill() {},
          }
        }
        const route = await resolveHostSession(session, '/tmp/opencode-worktree', fakeSpawn)
        console.log(JSON.stringify({ route, observed }))
        """
    )
    route = result["route"]
    observed = result["observed"]
    assert isinstance(route, dict) and route["status"] == "exact"
    assert route["session_sha256"] == "a" * 64
    assert route["task_handle_sha256"] == hashlib.sha256(
        b"taskh_route_candidate"
    ).hexdigest()
    assert isinstance(observed, dict)
    assert observed["argv"] == [
        "deeplaw",
        "knowledge",
        "task",
        "resolve-host-session",
        "--host",
        "opencode",
        "--session-sha256",
        "a" * 64,
        "--workspace",
        "/tmp/opencode-worktree",
    ]
    assert observed["options"]["stdout"] == "pipe"
    assert observed["options"]["stderr"] == "pipe"
    assert "DEEPSEEK_API_KEY" not in observed["options"]["env"]


def test_bun_context_re_resolves_route_and_binds_provider_hint_to_opaque_host_route() -> None:
    result = _bun_probe(
        """
        import { createOpenCodeHooks } from
          './adapters/opencode/plugins/deeplaw-native.ts'

        const sessionID = 'session-for-route'
        const session = await import('node:crypto').then(({ createHash }) =>
          createHash('sha256').update(sessionID).digest('hex'))
        let calls = 0
        const exact = {
          status: 'exact',
          session_sha256: session,
          gap: null,
          task_handle_sha256: 'b'.repeat(64),
          binding_sha256: 'c'.repeat(64),
          repository_sha256: 'd'.repeat(64),
          worktree_sha256: 'e'.repeat(64),
        }
        const gap = {
          status: 'gap',
          session_sha256: session,
          gap: 'route_wrong_worktree',
          task_handle_sha256: null,
          binding_sha256: null,
          repository_sha256: null,
          worktree_sha256: null,
        }
        const resolve = async (_session, _workspace) => {
          calls += 1
          return calls === 1 ? exact : gap
        }
        const hooks = createOpenCodeHooks('/tmp/opencode-worktree', resolve)
        const first = { system: [] }
        const second = { system: [] }
        await hooks['experimental.chat.system.transform']({ sessionID }, first)
        await hooks['experimental.chat.system.transform']({ sessionID }, second)
        console.log(JSON.stringify({ calls, first, second }))
        """
    )
    assert result["calls"] == 2
    first = json.dumps(result["first"], ensure_ascii=False)
    second = json.dumps(result["second"], ensure_ascii=False)
    assert "route_status=exact" in first
    assert "route_status=gap" in second
    assert "route_wrong_worktree" in second
    expected_hint = "host_route={host:opencode,session_sha256:" + hashlib.sha256(
        b"session-for-route"
    ).hexdigest() + "}"
    assert expected_hint in first
    assert expected_hint in second
    assert "taskh_" not in first + second
