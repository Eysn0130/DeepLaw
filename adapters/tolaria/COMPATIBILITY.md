# Tolaria compatibility report

Status: `integration_limited`, verified against source tag `v2026-07-22`, commit
`e2cd718a518cc96d1081b6ec3aabefe3b6c77199`, on 2026-08-07.

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

Not yet externally verified: a signed Tolaria desktop binary exercising the complete visible UI
workflow with a real Agent. That result must remain `not_executed` until the exact host binary and
model task are run; it is not inferred from this harness or promoted to Authority.
