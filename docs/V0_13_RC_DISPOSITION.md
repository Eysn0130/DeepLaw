# DeepLaw v0.13 source-candidate disposition

Decision: **source candidate under final local requalification; not released**.

DeepLaw remains package version `0.12.0`. No v0.13 tag, RC, GA, catalog, public release, or
published artifact is authorized. `release_gate_passed=false`, `claim_eligible=false`, and
`competitive_claim_eligible=false`.

This document records the release-safe disposition after the 2026-08-08 qualification and
remediation pass. Exact clean-commit report hashes and the fresh candidate-wheel hash are added by
the evidence-only handoff commit after the implementation commit exists; a commit cannot bind its
own identity.

## Scope and fixed boundaries

This work qualifies and minimally remediates the existing Evidence-Complete Living Wiki &
Persistent Agent Context source candidate. It does not reimplement v0.13 and does not change the
released package version.

The following locally reproduced defects have regression-backed fixes:

- Host subprocesses inherited the complete ambient environment, including an unrelated Secret
  canary. Host and MCP environments now use closed allowlists and separate Provider authentication.
- Query v6 scanned a global first-5,000 Statement prefix and silently discarded retrieval controls.
  Discovery is revision-bounded, the Statement pool remains capped at 512, all three controls are
  effective and receipt-bound, and the 64 KiB provider limit remains enforced.
- Relation predicate Schema/runtime sets drifted. They now share exact parity coverage.
- Query receipts lacked a defined lifecycle. Provider output now carries only `receipt_id`; local
  traces are bounded, expiring, redacted, integrity-bound, owner-deletable process state; ordinary
  queries never append Canonical Ledger mutations.
- Large Statement Evidence pages exceeded the 256 KiB Wiki bound. Deterministic 64-Statement
  shards preserve page registration, stable anchors, receipts, and canonical-page navigation.
- Source Summary counts joined two one-to-many tables into a quadratic intermediate. Independent
  indexed aggregates preserve the result without the cross product.
- Repeated Python context and scale reads performed full verification per request. They now reuse
  the canonical persistent verified snapshot and canonical Capsule builder; actual verification
  counts are instrumented by the qualification runners.

The exact root causes, impact, failure witnesses, Task Cards, and focused commands are in
`V0_13_P0_REMEDIATION_REPORT.md` and `work-plans/V0_13_AGENT_LEDGER.md`.

## Local qualification disposition

| Area | Status | Evidence boundary |
|---|---|---|
| Credential and Host isolation | `pass` (local deterministic) | Ambient canary is absent from Host/MCP child, argv, prompt, output, report, and artifact; required PATH/locale/temp variables remain available. No real Provider credential was used. |
| Query v6 Statement tail recall | `pass` (development) | Exact 5,001/10,000/100,000 synthetic positions, randomized identities, bounded candidate pool, hard provider limit, persistent runtime. |
| Query v6 controls and compatibility | `pass` (local contract) | CLI/MCP/Python/Schema/Receipt behavior plus v5 compatibility; invalid inputs fail rather than disappear. |
| Graph smoke and predicate parity | `pass` (smoke only) | `graph_hops=0/1/2`, hub/deep/cycle/contradiction/temporal/self-loop/dangling behavior and exact predicate parity. |
| 10k/100k Relation scale and 500/5,000 truncation | `not_executed` | No safe audited bulk Relation constructor exists; the 120 mutations/minute owner grant was not weakened for a benchmark. |
| Query Trace | `pass` for process-local contract | TTL, entry/byte bounds, rotation, redaction, integrity hash, identity invalidation, close/restart deletion; durable cross-process Trace is not implemented. |
| Wiki network | `pass` for local deterministic regressions | All 12 kinds, stable identity, sharding, Registry, Link Index, Resolver, profiles, owner-file preservation, incremental/full hash behavior. External identity Gold remains absent. |
| 1k/10k/100k construction | `development evidence executed` | Exact synthetic Assets, Wiki/Graph-derived rebuild, no whole-Vault full scan, file/Canvas/provider-byte measurements. Final clean-commit artifact binding is separate. |
| 10,000-request RSS / 8 readers | `development evidence executed` | 10,000/10,000 successful MCP queries, bounded observed current-RSS growth, unchanged Canonical Ledger, eight independent readers. This is not peak-RSS or cross-platform evidence. |
| Retrieval context utility | `partial` | Repository-visible development ablation exists; dense/graph, blind Human Gold, real model answer delta, and competitive comparators remain absent. |
| Legal retrieval | `partial` | Local evidence/Authority/temporal/OCR regressions execute; exact signed external 28-source Pack and independent critical-token review are `not_executed`. |
| Real Codex x3 | `not_executed` | Repository-external frozen corpus, Human Gold, exact isolated Host credentials, and final candidate were unavailable. |
| OpenCode/DeepSeek x3 | `blocked_not_executed` | Prior exposed key must be revoked; no new owner-only repository-external evaluation key was supplied. No real call was attempted. |
| 3 OS x Python 3.11/3.12/3.13 | `not_executed` | Only the local Darwin host is available; Linux/Windows evidence cannot be inferred. |
| Reproducible public release lifecycle | `not_executed` | No release version/tag/public artifact exists; formal SBOM, provenance, and public redownload therefore remain unavailable. |

## Mandatory external gates

The following are release-blocking and cannot be satisfied by a synthetic fixture or model output:

1. Three independent isolated real Codex runs against repository-external frozen Human Gold.
2. Compiler/evaluator isolation receipts proving Gold, scorer, expected IDs, and answers were not
   compiler-visible.
3. Exact signed 28-source legal Pack, critical-token review, citation/locator/version hard-zero
   failures, and no-answer/Gap scoring.
4. Fresh final blind holdout after the candidate freeze; any corpus used for diagnosis is
   development data thereafter.
5. 10k/100k governed Relation and explicit 500/5,000 truncation-position qualification.
6. Linux, Darwin, and Windows across Python 3.11/3.12/3.13 with mandatory no-skip lifecycle
   evidence.
7. Reproducible release artifacts, SBOM, provenance, signatures as applicable, and public
   redownload verification.
8. Cross-host OpenCode/DeepSeek runs only after Owner revocation of the old key and provision of a
   new owner-only evaluation secret outside the repository.

## Release decision

The locally reproduced P0 defects have minimal reviewed fixes and local regression coverage. That
does not make the source candidate evidence-complete for release. Sol therefore keeps package
version `0.12.0`, creates no v0.13 tag or release, and records the candidate as **not released**.

No claim of perfection, comprehensive leadership, SOTA, production readiness, legal correctness,
published RC, or GA is made.
