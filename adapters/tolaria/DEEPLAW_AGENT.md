# DeepLaw in Tolaria

Compatibility target: Tolaria `alpha-v2026.8.10-alpha.0001`
(`ab01faa6773136a58285d04cb81e2587c11bac85`, package `0.1.0`). The adapter status is
`integration_limited`: it uses Tolaria's documented stdio MCP and UI-action boundary, without
assuming a plugin API, forking Tolaria, or copying Tolaria code.

OpenCode and Tolaria share the same provider-facing `deeplaw.agent-context-envelope/v1` and the
same DeepLaw Domain APIs. The adapter maps active/open relative paths and only explicitly selected
text. A chat or conversation summary is not an evidence source, does not become Authority, and is
rejected when supplied as context. Every envelope is ephemeral with
`persistence_allowed=false`, `persistence_performed=false`, `authority=none`, and
`legal_authority=false`.

Use the four processes as separate capabilities:

- Tolaria MCP reads the active Vault/note and performs UI-only `open_note` or `refresh_vault`;
- `knowledge_support` returns bounded read-only DeepLaw context, evidence, Wiki navigation,
  freshness and gaps;
- `knowledge_sink` is enabled only with an owner-created grant and performs governed compilation,
  Synthesis refresh or explicit draft promotion;
- `law_support` reads the physically isolated Authoritative Pack and never writes Agent knowledge.

For an active-note task, read Tolaria's current context, map it to the shared Agent Context
Envelope, and call `knowledge_support` with the bounded task and purpose. Treat the returned
context as ephemeral: never save it automatically. Use `open_note` only with an exact relative path
returned by DeepLaw. Do not infer a canonical path from a title.

Tolaria note creation is limited to `drafts/` and `notes/`. Never use ordinary note tools to write
`.deeplaw/`, `sources/`, `knowledge/`, `memory/`, `wiki/`, or `canvas/`, or to create revision,
Authority, scope, sensitivity or source-reference metadata. A draft is not Knowledge. To promote a
query-synthesis draft, first validate it, then call the separately enabled `knowledge_sink` explicit
promotion operation with the exact draft ID, expected revision, idempotency key and owner grant.
After a successful receipt, ask Tolaria MCP to refresh the Vault and open the exact projected Wiki
path. A failed or incomplete receipt must never be reported as committed.

The frozen Tolaria release exposes no stable third-party active-note preview, exact page-intent, or
promotion UI extension point. The deterministic harness therefore reports those steps as
`not_executed` with `integration_limited`; it does not simulate them as passed.
