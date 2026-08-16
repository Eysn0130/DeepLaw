---
name: deeplaw-navigate-wiki
description: "Use when the user explicitly asks to browse or navigate a configured DeepLaw Living Wiki, read a derived page, inspect links, or browse a kind; trigger for Wiki navigation, not evidence citation or mutation."
---

# Navigate the Living Wiki

Use `knowledge_support` with `operation=wiki` and one explicit action: `page`, `backlinks`,
`outlinks`, `local_graph`, `browse_kind`, or `recent_changes`.

1. Resolve exactly one admitted `wiki_path` or `knowledge_id` for page and link actions. Use a
   bounded `limit`, cursor, scope, and maximum sensitivity.
2. Treat pages, links, Canvas views, and rankings as derived navigation only. Follow canonical
   revision IDs and call `operation=verify` before relying on a claim.
3. Keep source-derived evidence, autonomous knowledge, and Wiki navigation distinct. Do not mix
   read planes by default or write files.
