# DeepLaw in Tolaria

Compatibility target: Tolaria `v2026-06-23` (`b00fefef3fd503f2853445e085a44a8a371c3437`).
This adapter uses Tolaria's documented stdio MCP and UI-action boundary. It does not assume a
plugin API, fork Tolaria, or copy Tolaria code.

Use the four processes as separate capabilities:

- Tolaria MCP reads the active Vault/note and performs UI-only `open_note` or `refresh_vault`;
- `knowledge_support` returns bounded read-only DeepLaw context, evidence, Wiki navigation,
  freshness and gaps;
- `knowledge_sink` is enabled only with an owner-created grant and performs governed compilation,
  Synthesis refresh or explicit draft promotion;
- `law_support` reads the physically isolated Authoritative Pack and never writes Agent knowledge.

For an active-note task, read Tolaria's current context, map it to the closed Editor Context
Envelope, and call `knowledge_support` with `operation=editor_context`. Treat the returned context as
ephemeral: never save it automatically. Use `open_note` only with an exact relative path returned by
DeepLaw. Do not infer a canonical path from a title.

Tolaria note creation is limited to `drafts/` and `notes/`. Never use ordinary note tools to write
`.deeplaw/`, `sources/`, `knowledge/`, `memory/`, `wiki/`, or `canvas/`, or to create revision,
Authority, scope, sensitivity or source-reference metadata. A draft is not Knowledge. To promote a
query-synthesis draft, first validate it, then call the separately enabled `knowledge_sink` explicit
promotion operation with the exact draft ID, expected revision, idempotency key and owner grant.
After a successful receipt, ask Tolaria MCP to refresh the Vault and open the exact projected Wiki
path. A failed or incomplete receipt must never be reported as committed.
