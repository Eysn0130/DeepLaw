---
name: deeplaw-verify-evidence
description: "Use when the user explicitly asks DeepLaw to verify a source, fragment, Knowledge Revision, hash, receipt, or integrity claim before relying on it; trigger for evidence checking, not discovery alone or legal adjudication."
---

# Verify DeepLaw Evidence

Use only after explicit invocation and with an exact admitted identity.

1. Use `knowledge_support` `operation=source` with `source_action=get`, `fragment`, or `diff`
   for immutable source evidence; use `operation=verify` for an exact `asset_id` or
   `knowledge_id`.
2. Select scope and maximum sensitivity explicitly. Check revision IDs, hashes, locators,
   lifecycle, source integrity, receipts, and freshness before relying on content.
3. Stop on a failed or stale check. Preserve gaps and conflicts; never replace missing evidence
   with memory, ranking, Wiki text, or an unverified interpretation.
4. Keep this workflow read-only. Do not mutate sources, elevate authority, or treat verification
   as legal adjudication.
