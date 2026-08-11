---
name: deeplaw-compile-source
description: "Use only when the user explicitly asks to compile an admitted DeepLaw Source Revision into governed Knowledge Objects or a Living Wiki; trigger for source compilation and semantic ingestion, not ordinary retrieval or background learning."
---

# Compile an Admitted Source

Require a user-selected Source Revision and `confirm_no_case_data=true`.

1. Ask the owner to obtain the read-only handoff with `deeplaw knowledge compile handoff
   --source-revision-id <exact-id>`. Verify that it names `knowledge_support` and
   `knowledge_sink`, includes no grant, performed no write, and reports the exact Source status.
   Then read with `knowledge_support`: use `operation=source` to identify the exact revision and
   `operation=compilation` with `compilation_action=list_uncompiled` or `profile`.
2. Require a separately owner-created, scope-bound Grant for `knowledge_sink`. Never create,
   widen, inspect, or copy a Grant.
3. Use the explicit sink lifecycle: `begin_compilation`, `stage_semantic_observations`,
   `freeze_semantic_inventory`, `finalize_semantic_compilation`, `validate_compilation`,
   `commit_compilation`, then `resume_compilation` with projection requested. Supply fresh
   idempotency keys.
4. Let DeepLaw validate identity, source binding, provenance, lifecycle, and authority. Never
   write Markdown or databases directly, invoke a model automatically, or promote a draft.
5. Verify the committed revision and report partial status, gaps, conflicts, and projection state.
