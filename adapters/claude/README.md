# Optional Claude Code lifecycle adapter

This directory is an opt-in template. Merely checking out the repository does
not install or enable any Claude Code hook.

Install into an explicitly selected settings file:

```bash
python adapters/claude/install.py install \
  --settings /path/to/settings.json \
  --vault /path/to/local/vault \
  --workspace-identity workspace-name \
  --repository-identity repository-name
```

Use `uninstall --settings ...` to remove only entries carrying the exact
`deeplaw-claude-lifecycle-v1` marker. Other Claude settings and hooks remain
untouched. The installer never chooses or edits `~/.claude` implicitly, and an
existing marked conflict fails closed. For a settings-file install, it resolves
the checked-out `deeplaw_hook.py` to an explicit absolute command argument;
`${CLAUDE_PLUGIN_ROOT}` remains only in the uninstalled plugin template.

The six command hooks mirror Claude Code's current event names:
`UserPromptSubmit`, `PreCompact`, `PostCompact`, `PostToolUse`, `Stop`, and
`SessionEnd`. The hook reads one bounded JSON object from stdin and emits a
small JSON receipt. `UserPromptSubmit` injects a bounded Query Plan v6 capsule
through the official `hookSpecificOutput.additionalContext` field. Compact text
is used only as untrusted ephemeral query input. Claude Code's current
`PostCompact` event has no context-injection output, so that hook reports the
limitation and the next `UserPromptSubmit` reruns the query; it never claims the
post-compact capsule was delivered. Tool responses are hashed as bounded
canonical JSON and never emitted or stored. Stop/session events only suggest an
owner-reviewed backfill draft; they never call a Knowledge Sink or promote a
revision.

The adapter is no-model, local read-only, and network-free by code path. Missing
Vault/identity CLI configuration is an honest no-op. It does not read
the host transcript-file field, scan environment secrets, start a background process, or
persist host chat/tool payloads.
