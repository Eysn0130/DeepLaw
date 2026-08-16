---
name: deeplaw-refresh-synthesis
description: "Use only when the user explicitly asks to refresh a stale or invalidated DeepLaw Synthesis after source, knowledge, or relation changes; trigger for governed synthesis refresh, not ordinary query or automatic learning."
---

# Refresh a Governed Synthesis

Require explicit user direction, `confirm_no_case_data=true`, and the exact stale task.

1. Read `knowledge_support` with `operation=synthesis` and `synthesis_action=list_stale`, then
   inspect status, coverage, source revisions, and verification gaps.
2. Require a separately owner-created Grant for `knowledge_sink` (or an explicit owner CLI
   action) covering only this refresh and its scope. Never create, widen, inspect, or copy it.
3. Run `begin_synthesis_refresh`, stage a closed plan with exact revision and relation evidence,
   then `validate_synthesis_refresh`, `commit_synthesis_refresh`, and `resume_synthesis_refresh`.
   Use fresh idempotency keys and preserve unresolved duties.
4. Do not invoke a model automatically, invent provenance, write Markdown directly, or promote a
   draft. Report committed, pending, partial, or blocked status exactly.
