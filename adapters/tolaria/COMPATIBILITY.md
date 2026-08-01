# Tolaria compatibility report

Status: implementation target, verified against source tag `v2026-06-23`, commit
`b00fefef3fd503f2853445e085a44a8a371c3437`, on 2026-08-01.

The reviewed Tolaria release is files-first and offline-first. Its current supported integration
surface is an external stdio MCP server plus a WebSocket UI bridge. Durable external registrations
use standard `mcpServers` entries and resolve active Vaults at tool-call time from Tolaria's
`vaults.json`. The MCP surface includes active-Vault context, note reads/search, note creation and
UI actions including `open_note`. Tolaria's Agent context exposes an active note, open tabs and
explicit references with bounded body compaction.

DeepLaw therefore ships a config generator/merger, closed context mapping, UI-only open-note intent,
Agent instructions and a temporary-Vault harness. It does not claim a Tolaria plugin API or install
inside Tolaria. The merger preserves all unrelated settings and fails on a conflicting DeepLaw key.
Canonical DeepLaw roots remain read-only to Tolaria note tools; mutation stays in DeepLaw's owner-
granted Sink.

Verified locally without a model:

- exact upstream source/tag inspection;
- config preservation and collision failure;
- active-note snapshot to Editor Context Envelope mapping;
- read-only path rejection and UI-only Wiki open intent;
- temporary source-free Vault context retrieval with no Ledger mutation.

Not yet externally verified: a signed Tolaria desktop binary exercising the complete visible UI
workflow with a real Agent. That result must remain `not_executed` until the exact host binary and
model task are run; it is not inferred from this harness.
