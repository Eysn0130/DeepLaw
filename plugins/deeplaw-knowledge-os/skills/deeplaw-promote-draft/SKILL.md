---
name: deeplaw-promote-draft
description: "Use only when the user explicitly asks to review and promote a specific DeepLaw Knowledge draft; trigger for an identified draft and owner decision, not automatic learning, ordinary retrieval, or source authority changes."
---

# Promote an Admitted Draft

Require an exact `draft_id`, explicit owner direction, and `confirm_no_case_data=true`.

1. Read and verify the draft, its source references, scope, sensitivity, provenance, and duplicate
   checks with `knowledge_support` before proposing any action.
2. Require a separately owner-created Grant for `knowledge_sink` allowing only
   `promote_knowledge_draft`, or require an explicit owner CLI action. Never create, widen,
   inspect, or copy a Grant.
3. Require the recorded evaluator, reason, and any external or owner check. Preserve
   `origin=agent_derived`, `legal_authority=false`, scope, sensitivity, and immutable lineage.
4. Never promote automatically, invoke a model automatically, mutate Markdown directly, or turn
   ranking into Authority. Report rejection, quarantine, or successful promotion exactly.
