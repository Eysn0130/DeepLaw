# OpenCode task lifecycle adapter

This adapter is disabled by default. Copy `lifecycle.example.json` to an owner-only location,
replace every placeholder with the exact registered project task, Knowledge Vault, Git worktree,
and verified OpenCode version, then set `enabled` to `true`. Configure the exact supported
OpenCode lifecycle bridge to send only the native request seams `cli.run`, `cli.run.session`,
`cli.run.fork`, or `session/summarize`, or the native `session.compacted` event, plus an optional
opaque session hint to:

```bash
python adapters/opencode/lifecycle.py --config /owner/config/opencode-lifecycle.json
```

Disable by setting `enabled` to `false` or removing the Host event mapping. The adapter never
reads OpenCode memory, auth, transcript, hidden reasoning, or raw logs. It delegates to the same
DeepLaw task-continuity service and emits a separate content-minimized receipt. A checkpoint still
requires a separate owner grant. Client/case workspaces are not supported. OpenCode does not
automatically invoke this sidecar merely because it is installed: the owner-controlled launcher or
plugin must bind the native seam explicitly. The event-name and binary/version binding must be
re-verified before qualification; this README and local adapter tests are not real Host receipts.

## Native Bun plugin candidate

`plugins/deeplaw-native.ts` is a thin, local candidate for OpenCode `1.18.16` at source commit
`a3647eb025c7615159d417dcc49fc39fdaeba65b`, with config selector
`deepseek/deepseek-v4-flash` and expected response model ID `deepseek-v4-flash`. It binds the
native `chat.message`, `event`, `experimental.chat.system.transform`, and
`experimental.session.compacting` seams. The `event` seam accepts only
`session.created`, `session.updated`, and `session.compacted` lifecycle events.

The plugin reads only opaque session/event identity metadata. It never inspects `output.parts`,
prompt text, transcript/auth material, or model output. It injects at most 2 KiB of route status,
digests, explicit Gaps, and a prompt to call the existing read-only `knowledge_support` tool.
Compaction is read-only by default and reports `checkpoint_grant_missing`; it never binds a new
session, writes a checkpoint, starts a service, creates a database, or logs an event. Existing
session bindings are resolved through the read-only `deeplaw knowledge task resolve-host-session`
CLI seam when available; every context and compaction callback re-resolves that route so a stale,
forgotten, or wrong-worktree binding fails closed on the next call. When the session digest is
valid, the injected prompt requires `knowledge_support` `query` or `context` with the opaque
`host_route={host:opencode,session_sha256:<digest>}`; a Gap never claims an exact task binding.
`bind-host-session` is never called by this plugin. Its child environment is closed and does not
carry the DeepSeek key.

This is a candidate seam, not a real OpenCode qualification or release receipt. The owner must
review the exact installed plugin/source and provider configuration before using it; this adapter
does not change trust, authentication, or Secret state.
