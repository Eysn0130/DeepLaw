---
name: deeplaw-navigate-wiki
description: "Use when the user explicitly asks to browse or navigate a configured DeepLaw Living Wiki, read a derived page, inspect links, or browse a kind; trigger for Wiki navigation, not evidence citation or mutation."
---

# Navigate the Living Wiki

Use the single `knowledge_support` leaf with input v8 `operation=read`.

1. Start from an admitted query/context reference. Set `target.kind=wiki`, `knowledge_id`,
   and the exact current `revision_id`; set scope and maximum sensitivity explicitly.
   This reads generated knowledge pages only. Index pages, backlinks, outlinks, and broad
   navigation remain owner CLI/internal services, not advertised v8 read operations.
2. Set `max_chars` (200–12000). Continue with `next_offset` and the returned `content_sha256`;
   stop if the revision, content, or current Admission changes. Reads do not build projections.
3. Treat the page as derived navigation. Follow its admitted Source Revision and fragment
   references using `target.kind=source_fragment`; Wiki access cannot bypass Source policy.
4. Preserve the reported read budget. It covers successful canonical read content in this MCP
   lifespan, not query/context, errors, reconnects, total wire bytes, or the entire Agent task.
   Do not write files or infer Authority from navigation.
