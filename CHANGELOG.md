# Changelog

All notable product changes are recorded here. DeepLaw 2.0 is the product name; Python package
versions follow semantic versioning independently.

## 0.6.0 — 2026-07-27

### Added

- Logical `source_key` identities and immutable source-version lifecycle records.
- Source list/show/verify/diff/update/remove CLI operations and bounded directory ingestion with
  dry-run manifests and explicit partial-success semantics.
- Optional deterministic typed proposals for explicit decision, constraint, procedure, rule,
  fact, lesson, and question headings.
- Review queue, exact source membership manifests, approve/reject operations, and immutable local
  Review Receipts.
- Task Run Receipts, structured Feedback Ledger records, regression-case promotion, and feedback
  replay.
- Closed JSON Schemas for Review Receipt, Run Receipt, and Feedback Ledger artifacts.
- Additive legacy Vault control-plane migration with verified pre-apply backup, post-apply
  verification, and recoverable atomic rollback.
- Stable `json`, `jsonl`, and human-readable Knowledge CLI output modes.
- macOS and Windows CLI/MCP smoke jobs in CI; Windows ACL equivalence remains a separate gate.

### Fixed

- Context compilation no longer deduplicates equal section titles across different sources.
- Compiled section-group identities preserve distinct repeated headings within one source while
  still collapsing oversized parts of the same section.
- Every selected source-bound Capsule item receives at least one compact source reference.
- Capsule verification rejects source-bound items whose embedded provenance was removed, even
  when the Capsule is resealed with a matching digest.
- Task Run Receipts can only be created from a real, currently verified Capsule and must match its
  exact identity, audit anchor, Asset inventory, and embedded source inventory.
- Re-reviewing an already active exact source manifest always creates an immutable Review Receipt.
- POSIX permission diagnosis scans the complete stored-source set before returning `verified`;
  bounded report detail no longer weakens the security verdict.
- Batch source approval now requires the exact current review-manifest SHA-256 on every path.
- Feedback asset classifications are limited to Assets actually present in the bound Run Capsule.
- Legacy control migrations remain replay-valid after later source lifecycle events.
- Pending successor source versions cannot be individually approved and partially replace active
  knowledge; exact source review is required for atomic activation.
- External JSONL evaluator, MCP inspection Schema, and `.dlk` v1 export remain compatible with the
  expanded control plane.

### Security and claims

- Agent MCP surfaces remain read-only; none of the new administration operations are exposed.
- Local review receipts are hash/audit protected but unsigned; `.dlk` v1 still provides content
  integrity only.
- Windows ACL equivalence and external cross-system performance remain unverified.
- The pinned optional document-engine dependency is unchanged. The checked-in OpenVEX statement
  was reissued for the v0.6.0 product identity after the same closed execution path was re-audited.

## 0.5.0 — 2026-07-26

- Added the isolated general Knowledge Asset core, bounded Knowledge Capsules, deterministic
  Markdown projection, unsigned `.dlk` quarantine workflow, optional local Discovery Index,
  read-only `knowledge_support`, and the frozen external-evaluation protocol.
- Preserved the independent Chinese Legal Pack, signed official catalog workflow, immutable
  releases, `law_support`, and user-private legal-reference scope.
