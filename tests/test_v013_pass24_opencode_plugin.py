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
CONTEXT_BRIDGE_PATH = PLUGIN / "context-bridge.json"
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
        "checkpoint_grant_missing",
        "resolve-host-continuity",
        "candidate seam",
    ):
        assert required in readme


def test_context_bridge_uses_the_exact_opencode_pin_and_marks_compaction_experimental() -> None:
    bridge = json.loads(CONTEXT_BRIDGE_PATH.read_text(encoding="utf-8"))
    assert bridge["exact_upstream"] == {
        "name": "OpenCode",
        "version": "1.18.16",
        "commit": "a3647eb025c7615159d417dcc49fc39fdaeba65b",
        "plugin_api_status": "version_pinned_experimental",
        "compaction_hook_stability": "experimental_exact_version_only",
        "stable_active_note_preview_promote": False,
    }


def test_plugin_source_is_thin_and_never_injects_route_identity() -> None:
    source = SOURCE_PATH.read_text(encoding="utf-8")
    for seam in (
        '"chat.message"',
        "event:",
        '"experimental.chat.system.transform"',
        '"experimental.session.compacting"',
        "session.created",
        "session.updated",
        "session.compacted",
        "deeplaw.opencode-native-event-observation/v1",
        "Bun",
        "checkpoint_grant_missing",
        "deeplaw.host-continuity-capsule/v1",
        "resolve-host-continuity",
    ):
        assert seam in source
    assert "output.parts" not in source
    assert "bind-host-session" not in source
    assert "resolve-host-session" not in source
    assert "host-session-route-result" not in source
    assert "task_handle_sha256" not in source
    assert "binding_sha256" not in source
    assert "repository_sha256" not in source
    assert "worktree_sha256" not in source
    assert "host_route" not in source
    assert "console.log" not in source
    assert "DEEPSEEK_API_KEY" not in source


@pytest.mark.qualification
def test_bun_helpers_cover_parent_identity_and_provider_safe_native_seams() -> None:
    result = _bun_probe(
        """
        import { observeEvent, createOpenCodeHooks, childEnvironmentPolicy } from
          './adapters/opencode/plugins/deeplaw-native.ts'

        const withParent = observeEvent({
          event: {
            id: 'event-1',
            type: 'session.created',
            properties: {
              sessionID: 'child-session',
              info: {
                id: 'child-session',
                parentID: 'parent-session',
                title: 'fork title',
              },
            },
          },
        })
        const withoutParent = observeEvent({
          type: 'session.created',
          info: { id: 'child-session', title: 'same order as another session' },
        })
        const unsupported = observeEvent({ type: 'session.deleted', info: { id: 'secret' } })
        process.env.DEEPLAW_KNOWLEDGE_VAULT = '/tmp/deeplaw-vault'
        process.env.DEEPSEEK_API_KEY = 'must-not-cross'
        const capsule = {
          schema_version: 'deeplaw.host-continuity-capsule/v1',
          status: 'gap',
          statements: [],
          gaps: [{ code: 'vault_unavailable' }],
          conflicts: [],
          write_performed: false,
        }
        const resolve = async () => capsule
        const modelObservations = []
        const hooks = await createOpenCodeHooks(
          '/tmp/opencode-worktree',
          resolve,
          async (value) => modelObservations.push(value),
        )
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
        await hooks.event({
          type: 'session.created',
          info: { id: 'chat-session', title: 'native title canary' },
        })
        await hooks.event({
          type: 'session.updated',
          info: { id: 'chat-session', title: 'native update canary' },
        })
        await hooks.event({
          type: 'session.compacted',
          info: { id: 'chat-session', title: 'native compact canary' },
          parts: [{ type: 'reasoning', text: 'native reasoning canary' }],
        })
        await hooks.event({
          type: 'message.updated',
          properties: {
            info: {
              id: 'message-secret',
              sessionID: 'chat-session',
              role: 'assistant',
              providerID: 'deepseek',
              modelID: 'deepseek-v4-flash',
              summary: false,
              finish: 'stop',
              tokens: {
                input: 10,
                output: 4,
                reasoning: 1,
                cache: { read: 2, write: 0 },
              },
            },
            parts: [{ type: 'reasoning', text: 'reasoning-canary' }],
          },
        })
        console.log(JSON.stringify({
          withParent,
          withoutParent,
          unsupported,
          hookKeys: Object.keys(hooks).sort(),
          system,
          compact,
          modelObservations,
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

    system = result["system"]
    compact = result["compact"]
    assert isinstance(system, dict) and isinstance(compact, dict)
    serialized = json.dumps(
        {"system": system, "compact": compact}, ensure_ascii=False, sort_keys=True
    )
    assert "prompt-canary" not in serialized
    assert "raw-prompt-canary" not in serialized
    assert "fork title" not in serialized
    assert "same order" not in serialized
    assert "must-not-cross" not in serialized
    model_observations = result["modelObservations"]
    assert isinstance(model_observations, list) and len(model_observations) == 6
    native_observations = [
        item
        for item in model_observations
        if item.get("schema_version")
        == "deeplaw.opencode-native-event-observation/v1"
    ]
    assert [item["event_type"] for item in native_observations] == [
        "session.created",
        "session.updated",
        "session.compacted",
    ]
    assert all(item["status"] == "observed" for item in native_observations)
    assert all(
        set(item)
        == {
            "schema_version",
            "event_type",
            "session_sha256",
            "parent_session_sha256",
            "parent_gap",
            "status",
            "gap",
        }
        for item in native_observations
    )
    native_serialized = json.dumps(native_observations, ensure_ascii=False, sort_keys=True)
    for sensitive in (
        "native title canary",
        "native update canary",
        "native compact canary",
        "native reasoning canary",
        "parts",
        "prompt",
        "providerID",
    ):
        assert sensitive not in native_serialized
    delivery_observations = [
        item
        for item in model_observations
        if item.get("schema_version")
        == "deeplaw.opencode-continuity-delivery-observation/v1"
    ]
    assert [item["event_type"] for item in delivery_observations] == [
        "experimental.chat.system.transform",
        "experimental.session.compacting",
    ]
    assert all(
        item["session_sha256"]
        == hashlib.sha256(b"chat-session").hexdigest()
        for item in delivery_observations
    )
    assert delivery_observations[0]["status"] == "gap"
    assert delivery_observations[0]["gap_codes"] == ["vault_unavailable"]
    assert delivery_observations[1]["gap_codes"] == [
        "checkpoint_grant_missing",
        "vault_unavailable",
    ]
    model_observation = next(
        item
        for item in model_observations
        if item.get("schema_version") == "deeplaw.opencode-model-observation/v1"
    )
    assert isinstance(model_observation, dict)
    assert model_observation["provider_id"] == "deepseek"
    assert model_observation["model_id"] == "deepseek-v4-flash"
    assert model_observation["session_sha256"] == hashlib.sha256(
        b"chat-session"
    ).hexdigest()
    assert model_observation["message_sha256"] == hashlib.sha256(
        b"message-secret"
    ).hexdigest()
    assert "parts" not in model_observation
    assert "reasoning-canary" not in json.dumps(model_observation)
    assert len(json.dumps(system, ensure_ascii=False).encode("utf-8")) <= 2048
    assert len(json.dumps(compact, ensure_ascii=False).encode("utf-8")) <= 2048
    system_capsule = json.loads(system["system"][0])
    compact_capsule = json.loads(compact["context"][0])
    assert system["system"][0] == json.dumps(
        system_capsule, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    )
    assert compact["context"][0] == json.dumps(
        compact_capsule, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    )
    assert system_capsule["schema_version"] == "deeplaw.host-continuity-capsule/v1"
    assert compact_capsule["schema_version"] == "deeplaw.host-continuity-capsule/v1"
    assert {gap["code"] for gap in compact_capsule["gaps"]} == {
        "vault_unavailable",
        "checkpoint_grant_missing",
    }
    assert compact_capsule["write_performed"] is False
    assert "session_sha256" not in serialized
    assert "repository_sha256" not in serialized
    assert "worktree_sha256" not in serialized
    assert "task_handle" not in serialized
    assert "receipt" not in serialized
    assert "host_route" not in serialized

    env = result["env"]
    assert isinstance(env, dict)
    assert env == {
        "PATH": env["PATH"],
        "LANG": "C",
        "LC_ALL": "C",
        "DEEPLAW_KNOWLEDGE_VAULT": "/tmp/deeplaw-vault",
    }


@pytest.mark.qualification
def test_bun_continuity_resolution_uses_jsonl_capsule_and_closed_env() -> None:
    result = _bun_probe(
        """
        import { resolveHostContinuity } from
          './adapters/opencode/plugins/deeplaw-native.ts'

        const stream = (text) => new ReadableStream({
          start(controller) {
            controller.enqueue(new TextEncoder().encode(text))
            controller.close()
          },
        })
        process.env.DEEPLAW_KNOWLEDGE_VAULT = '/tmp/deeplaw-vault'
        process.env.OPENAI_API_KEY = 'must-not-cross'
        const session = 'a'.repeat(64)
        let observed
        const fakeSpawn = (argv, options) => {
          observed = { argv, options }
          return {
            stdout: stream(JSON.stringify({
              schema_version: 'deeplaw.host-continuity-capsule/v1',
              status: 'gap',
              statements: [],
              gaps: [{ code: 'workspace_diverged', message: 'checkpoint withheld' }],
              conflicts: [],
              write_performed: false,
            })),
            stderr: stream('ignored-error-output'),
            exited: Promise.resolve(0),
            kill() {},
          }
        }
        const capsule = await resolveHostContinuity(session, '/tmp/opencode-worktree', fakeSpawn)
        console.log(JSON.stringify({ capsule, observed }))
        """
    )
    capsule = result["capsule"]
    observed = result["observed"]
    assert isinstance(capsule, dict)
    assert capsule["status"] == "gap"
    assert capsule["gaps"][0]["code"] == "workspace_diverged"
    assert isinstance(observed, dict)
    assert observed["argv"] == [
        "deeplaw",
        "knowledge",
        "--format",
        "jsonl",
        "task",
        "resolve-host-continuity",
        "--vault",
        "/tmp/deeplaw-vault",
        "--host",
        "opencode",
        "--session-sha256",
        "a" * 64,
        "--workspace",
        "/tmp/opencode-worktree",
    ]
    assert observed["options"]["cwd"] == "/tmp/opencode-worktree"
    assert observed["options"]["stdin"] == "ignore"
    assert observed["options"]["stdout"] == "pipe"
    assert observed["options"]["stderr"] == "pipe"
    assert observed["options"]["env"] == {
        "PATH": observed["options"]["env"]["PATH"],
        "LANG": "C",
        "LC_ALL": "C",
        "DEEPLAW_KNOWLEDGE_VAULT": "/tmp/deeplaw-vault",
    }


@pytest.mark.qualification
def test_bun_capsule_parser_rejects_route_hashes_paths_secrets_and_extra_keys() -> None:
    result = _bun_probe(
        """
        import { parseContinuityOutput } from
          './adapters/opencode/plugins/deeplaw-native.ts'

        const valid = {
          schema_version: 'deeplaw.host-continuity-capsule/v1',
          status: 'admitted',
          statements: [{
            content: [
              'Continue the bounded implementation plan.',
              'NEXT_ACTION: verify the public seam.',
            ].join('\\n'),
            authority: 'agent_derived',
            legal_authority: false,
            valid_from: null,
            valid_to: null,
            citations: [{ locator: 'section 1' }],
          }],
          gaps: [],
          conflicts: [],
          write_performed: false,
        }
        const parse = (value) => parseContinuityOutput(
          new TextEncoder().encode(JSON.stringify(value)),
        ) !== null
        const route = {
          schema_version: 'deeplaw.host-session-route-result/v2',
          status: 'exact',
          session_sha256: 'a'.repeat(64),
        }
        const withExtra = { ...valid, receipt_id: 'receipt-private' }
        const withHash = {
          ...valid,
          statements: [{ ...valid.statements[0], content: 'id=' + 'a'.repeat(64) }],
        }
        const withPath = {
          ...valid,
          statements: [{
            ...valid.statements[0],
            content: 'Continue from /Users/private/task.txt',
          }],
        }
        const withGenericPath = {
          ...valid,
          statements: [{
            ...valid.statements[0],
            content: 'Continue from /custom/private/task.txt',
          }],
        }
        const withSecret = {
          ...valid,
          statements: [{ ...valid.statements[0], content: 'api_key=sk-test-secret-material' }],
        }
        const withAuthorization = {
          ...valid,
          statements: [{ ...valid.statements[0], content: 'Authorization: secret-material-value' }],
        }
        const withBearer = {
          ...valid,
          statements: [{ ...valid.statements[0], content: 'Bearer secret-material-value' }],
        }
        const withBadCode = {
          ...valid,
          status: 'gap',
          statements: [],
          gaps: [{ code: 'Bad code' }],
        }
        const tooManyStatements = {
          ...valid,
          statements: [valid.statements[0], valid.statements[0], valid.statements[0]],
        }
        console.log(JSON.stringify({
          valid: parse(valid),
          route: parse(route),
          withExtra: parse(withExtra),
          withHash: parse(withHash),
          withPath: parse(withPath),
          withGenericPath: parse(withGenericPath),
          withSecret: parse(withSecret),
          withAuthorization: parse(withAuthorization),
          withBearer: parse(withBearer),
          withBadCode: parse(withBadCode),
          tooManyStatements: parse(tooManyStatements),
        }))
        """
    )
    assert result == {
        "valid": True,
        "route": False,
        "withExtra": False,
        "withHash": False,
        "withPath": False,
        "withGenericPath": False,
        "withSecret": False,
        "withAuthorization": False,
        "withBearer": False,
        "withBadCode": False,
        "tooManyStatements": False,
    }


@pytest.mark.qualification
def test_bun_hooks_re_resolve_capsule_and_precompact_only_adds_gap() -> None:
    result = _bun_probe(
        """
        import { createOpenCodeHooks } from
          './adapters/opencode/plugins/deeplaw-native.ts'

        let calls = 0
        const exact = {
          schema_version: 'deeplaw.host-continuity-capsule/v1',
          status: 'admitted',
          statements: [{
            content: 'Continue the verified implementation plan.',
            authority: 'agent_derived',
            legal_authority: false,
            valid_from: null,
            valid_to: null,
            citations: [],
          }],
          gaps: [],
          conflicts: [],
          write_performed: false,
        }
        const resolve = async () => {
          calls += 1
          return calls === 1
            ? exact
            : {
              ...exact,
              status: 'gap',
              statements: [],
              gaps: [{ code: 'route_wrong_worktree' }],
            }
        }
        const hooks = createOpenCodeHooks('/tmp/opencode-worktree', resolve)
        const first = { system: [] }
        const compact = { context: [] }
        await hooks['experimental.chat.system.transform']({ sessionID: 'session-for-route' }, first)
        await hooks['experimental.session.compacting']({ sessionID: 'session-for-route' }, compact)
        console.log(JSON.stringify({ calls, first, compact }))
        """
    )
    assert result["calls"] == 2
    first = result["first"]
    compact = result["compact"]
    assert isinstance(first, dict) and isinstance(compact, dict)
    first_capsule = json.loads(first["system"][0])
    compact_capsule = json.loads(compact["context"][0])
    assert first_capsule["status"] == "admitted"
    assert first_capsule["statements"][0]["content"] == (
        "Continue the verified implementation plan."
    )
    assert compact_capsule["status"] == "gap"
    assert compact_capsule["gaps"] == [
        {"code": "route_wrong_worktree"},
        {"code": "checkpoint_grant_missing"},
    ]
    serialized = json.dumps(result, ensure_ascii=False)
    assert "session_sha256" not in serialized
    assert "repository_sha256" not in serialized
    assert "worktree_sha256" not in serialized
    assert "task_handle" not in serialized
    assert "receipt" not in serialized
    assert "path" not in serialized
    assert "log" not in serialized
    assert "host_route" not in serialized
    assert "checkpoint(" not in serialized


@pytest.mark.qualification
def test_bun_default_export_keeps_v1_loader_shape() -> None:
    result = _bun_probe(
        """
        import plugin from './adapters/opencode/plugins/deeplaw-native.ts'
        console.log(JSON.stringify({
          id: plugin.id,
          server_type: typeof plugin.server,
        }))
        """
    )
    assert result == {"id": "deeplaw-native", "server_type": "function"}
