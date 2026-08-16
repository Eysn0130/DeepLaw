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
