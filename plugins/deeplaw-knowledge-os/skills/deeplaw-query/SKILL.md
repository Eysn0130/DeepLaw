---
name: deeplaw-query
description: "Use when the user explicitly asks to query a configured local DeepLaw Knowledge OS, answer from governed knowledge, or compile a bounded task context; trigger for task questions and context requests, not ordinary coding or legal adjudication."
---

# Query DeepLaw Knowledge

Use only after explicit user invocation. Keep every read bounded and preserve provenance.

1. Call `knowledge_support` with `operation=query`, `purpose=answer`, and a concrete bounded
   query for compiled-first reuse.
2. Use `operation=context` only when the user asks for a task capsule; require
   `confirm_no_case_data=true` and provide a short task and goal.
3. Select scope and maximum sensitivity explicitly. The current public input does not accept
   a read plane selector; the Context Compiler admits and labels the selected partitions.
4. Set `limit`, `max_chars`, `max_tokens`, `max_sources`, and `graph_hops` deliberately. Preserve
   gaps, conflicts, receipts, revision IDs, and authority partitions in the answer.
5. Use `operation=read` with an exact `target` from the response to inspect a registered knowledge
   body or source fragment; use `operation=explain` with its `receipt_id` for the query receipt.
   Preserve integrity and freshness gaps. Never write, promote, or treat rank as authority.
