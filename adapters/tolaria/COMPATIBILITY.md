# Tolaria compatibility report

Status: `integration_limited`, reviewed at release tag `v2026-08-11`, annotated-tag commit
`cb45f26649a7500e0bdb5dd0b8f0412e9c1daf4d` (package `0.1.0`), on 2026-08-11.

The reviewed Tolaria release is files-first and offline-first. Its current supported integration
surface is an external stdio MCP server plus a WebSocket UI bridge. Durable external registrations
use standard `mcpServers` entries and resolve active Vaults at tool-call time from Tolaria's
`vaults.json`. The MCP surface includes active-Vault context, note reads/search, note creation and
UI actions including `open_note`. Tolaria's Agent context exposes an active note, open tabs and
explicit references with bounded body compaction. DeepLaw maps these paths and any explicitly
selected text to the shared `deeplaw.agent-context-envelope/v1`; an active-note body is not
implicitly provider content.

DeepLaw therefore ships a config generator/merger, closed context mapping, UI-only open-note intent,
Agent instructions and a temporary-Vault harness. It does not claim a Tolaria plugin API or install
inside Tolaria. The merger preserves all unrelated settings and fails on a conflicting DeepLaw key.
The reviewed release has no stable third-party extension point for active-note preview, exact Wiki
page intent, or promotion UI, so those steps remain `not_executed` in the harness rather than being
reported as a successful Tolaria integration.
Canonical DeepLaw roots remain read-only to Tolaria note tools; mutation stays in DeepLaw's owner-
granted Sink.

Verified locally without a model:

- exact upstream source/tag inspection;
- config preservation and collision failure;
- active-note snapshot to the shared Agent Context Envelope mapping;
- read-only path rejection and UI-only Wiki open intent;
- temporary source-free Vault Query Plan v6 and legacy context preview with no Ledger mutation;
- explicit `integration_limited` receipt naming the missing stable Tolaria extension point.

Not yet externally verified: the released Tolaria desktop binary exercising the complete visible
UI workflow with a real Agent. Tolaria Desktop was not installed on this machine during Pass 11.
That result remains `not_executed`; it is not inferred from source inspection or the harness and is
not promoted to Authority.
