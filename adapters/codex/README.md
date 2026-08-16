# Codex task lifecycle adapter

This adapter is disabled by default. Copy `lifecycle.example.json` to an owner-only location,
replace every placeholder with the exact registered project task, Knowledge Vault, Git worktree,
and verified Codex version, then set `enabled` to `true`. Configure the official Codex lifecycle
event bridge to send only `thread/start`, `thread/resume`, `thread/fork`, or
`thread/compact/start` plus an optional opaque thread hint to:

```bash
python adapters/codex/lifecycle.py --config /owner/config/codex-lifecycle.json
```

Disable by setting `enabled` to `false` or removing the Host event mapping. The adapter never
reads Codex memory, auth, transcript, hidden reasoning, or raw logs. It rebinds the configured
Vault/project/task/repository/worktree through DeepLaw and emits a separate
`native-host-lifecycle-receipt/v1`; it does not change the v2 task result's
`native_host_lifecycle_observed=false`. A checkpoint remains a separate owner-granted CLI or
`knowledge_sink` write. Client/case workspaces are not supported. Codex App Server does not
automatically invoke this sidecar merely because it is installed: the owner-controlled App Server
client must bind the request seam explicitly. This README and local adapter tests are not real Host
receipts.
