# DeepLaw editor bridge contracts

Status: **vNext implementation candidate**, 2026-08-01. DeepLaw now includes an installable thin
Obsidian plugin and an executable Tolaria adapter. It still does not fork either editor or make an
editor authoritative.

## Editor and Agent Context Envelopes

Obsidian and Tolaria map transient UI state to
[`editor-context-envelope.v1.schema.json`](../contracts/editor-context-envelope.v1.schema.json):
frontend/version, exact Vault identity, active-note identity/hash, optional bounded selection and
range, tabs, explicit references, links, Canvas/Bases identity, user intent, scope, maximum
sensitivity and budgets.

DeepLaw verifies the closed schema, selection/range binding and exact Vault identity. It uses user
intent, the explicit selection and at most eight explicit note references for a bounded,
compiled-first query. Tabs, backlinks, outlinks and view metadata remain available for a future
explicit routing policy; they are not silently dumped into provider context.

The result is always `ephemeral_context=true` and `persistence_performed=false`. Even when the
caller sets `persistence_allowed=true`, a separate backfill or Sink request is required to create
durable knowledge.

The v0.13 host-neutral
[`agent-context-envelope.v1.schema.json`](../contracts/agent-context-envelope.v1.schema.json) is a
separate provider-facing lifecycle contract. It binds a bounded task/goal, opaque workspace and
repository identities, commit/branch identities, active/open/current files, explicit selection,
tabs, note/tool digests, purpose, policy and token budget. Obsidian, OpenCode and Tolaria generate
the same content hash for the same normalized host state. The Editor envelope remains a supported
UI-state input; it is not silently relabelled as an Agent envelope. Both are ephemeral, contain no
Grant, and perform no Ledger write.

## Ownership

| Surface | Editor owns | DeepLaw owns |
| --- | --- | --- |
| UI | active note, selection, tabs, link/navigation state, open/highlight/refresh | bounded result and evidence receipts |
| Drafts | user/Agent editing in declared writable roots | validation before canonical promotion |
| Sources | Obsidian may place a file in `sources/inbox` | registration, immutable bytes, Source Revision and compilation |
| Knowledge | display/open | stable ID, revision, CAS, Ledger, Authority, source binding and mutation |
| Derived views | display/open/refresh | Wiki, graph, Canvas, indexes and manifests |

No editor text or plugin manifest may create `deeplaw_id`, revision identity, Authority, scope,
sensitivity, source references, Ledger events or capabilities.

## Obsidian contract

[`adapters/obsidian/manifest.json`](../adapters/obsidian/manifest.json) permits writes only under:

```text
drafts/
notes/
sources/inbox/
```

`knowledge/` and `memory/` require Sink/reconcile. `wiki/` and `canvas/` are derived read-only
views. `.deeplaw/` is forbidden. The production bridge under `adapters/obsidian/plugin/` waits for
`workspace.onLayoutReady` before registering file events, preventing startup enumeration from being
mistaken for new uploads. It uses argument-array process spawning with hard output/time limits,
exposes explicit commands only, and never performs background compilation or network access. The
v0.13 operator flow uses source/run/page pickers and exact paginated link navigation rather than
asking the user to type internal IDs. Its
release bundle consists of `main.js`, `manifest.json` and `styles.css`; the mock remains a contract
fixture.

This design follows Obsidian's public plugin lifecycle and API boundary rather than treating an
ordinary Vault file as a trusted database. References:
[Obsidian plugin load-time guide](https://docs.obsidian.md/plugins/guides/load-time) and
[Obsidian API repository](https://github.com/obsidianmd/obsidian-api).

## Tolaria contract

DeepLaw does not fork Tolaria and does not put knowledge business logic into it.
[`adapters/tolaria/manifest.json`](../adapters/tolaria/manifest.json) permits only `drafts/` and
`notes/` writes. `.deeplaw`, source evidence, canonical knowledge/memory, Wiki and Canvas are
read-only.

Recommended processes remain separate:

```text
Tolaria MCP          → UI and active workspace context
knowledge_support    → read-only context, evidence, gaps and freshness
knowledge_sink       → owner-granted canonical mutation
law_support          → isolated authoritative legal evidence
```

The executable adapter under `adapters/tolaria/` merges namespaced MCP entries into a separate
output file without overwriting existing settings, maps Tolaria's documented active-note snapshot
to the closed Editor Context Envelope, and emits UI-only `open_note` intents. Its Agent guide keeps
ordinary note creation in `drafts/` or `notes/`; explicit promotion uses the independently enabled
DeepLaw Sink. The new configuration is owner-only: POSIX uses mode `0600`, while Windows applies
and independently verifies a protected native owner ACL. A source-free temporary-Vault harness
proves the mapping remains ephemeral and does not mutate the Ledger.

The flow is: Tolaria supplies explicit context → DeepLaw retrieves or compiles → an authorized Sink
commits canonical state → Tolaria refreshes/opens the exact projected path. Tolaria's note tools
never write canonical roots. This preserves its workspace/Agent abstractions without importing
DeepLaw's governance into the frontend. The compatibility target is Tolaria
`alpha-v2026.8.10-alpha.0001`, commit
`ab01faa6773136a58285d04cb81e2587c11bac85` (package `0.1.0`). The exact upstream release exposes no stable
third-party active-note preview or promotion UI extension point, so the executable harness reports
the full product loop as `integration_limited` rather than simulating it. References:
[Tolaria repository](https://github.com/refactoringhq/tolaria),
[Tolaria abstractions](https://github.com/refactoringhq/tolaria/blob/main/docs/ABSTRACTIONS.md) and
[Tolaria releases](https://tolaria.md/releases/).

## Security and conflict behavior

- Paths are canonical relative POSIX paths; absolute paths, backslashes, `.`/`..`, escapes and
  undeclared roots fail closed.
- Context is capped by note, selection, character and provider budgets.
- A mismatched Vault identity is rejected before retrieval.
- Source/editor content remains untrusted data and cannot override host or grant policy.
- Canonical edits from ordinary editors are reconciled through the shared coordinator with base
  revision/CAS checks; conflicts are preserved rather than overwritten.
- Bridge context does not append a Ledger event.

Contract, setup, path-boundary and temporary-Vault tests live in
[`tests/test_editor_bridges.py`](../tests/test_editor_bridges.py). These deterministic tests are not
represented as a signed-editor-binary or real-model task result.
