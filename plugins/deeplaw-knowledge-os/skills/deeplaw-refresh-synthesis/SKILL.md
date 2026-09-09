---
name: deeplaw-refresh-synthesis
description: "Use only when the user explicitly asks to refresh a stale or invalidated DeepLaw Synthesis after source, knowledge, or relation changes; trigger for governed synthesis refresh, not ordinary query or automatic learning."
---

# Refresh a Governed Synthesis

Require explicit user direction, `confirm_no_case_data=true`, and the exact stale task.

1. Obtain stale synthesis status, coverage, source revisions, and verification gaps through
   the explicit owner CLI or a separately declared internal compatibility surface. Public
   input v8 does not advertise synthesis/list_stale; report a capability gap when no supported
   status path is available rather than submitting an unadvertised operation.
2. Require a separately owner-created Grant for `knowledge_sink` (or an explicit owner CLI
   action) covering only this refresh and its scope. Never create, widen, inspect, or copy it.
3. Run `begin_synthesis_refresh`, stage a closed plan with exact revision and relation evidence,
   then `validate_synthesis_refresh`, `commit_synthesis_refresh`, and `resume_synthesis_refresh`.
   Use fresh idempotency keys and preserve unresolved duties.
4. Do not invoke a model automatically, invent provenance, write Markdown directly, or promote a
   draft. Report committed, pending, partial, or blocked status exactly.
