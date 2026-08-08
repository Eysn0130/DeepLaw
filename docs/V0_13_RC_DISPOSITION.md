# DeepLaw v0.13 source-candidate disposition

Decision: **local source-candidate requalification complete; external gates unmet; not released**.

DeepLaw remains package version `0.12.0`. No v0.13 tag, RC, GA, catalog, public release, or
published artifact is authorized. `release_gate_passed=false`, `claim_eligible=false`, and
`competitive_claim_eligible=false`.

This document records the release-safe disposition after the 2026-08-08 qualification and
remediation pass. The implementation candidate is commit
`bb6a942970186f03ea41e108a2eceaaca54e3bcb`, tree
`8817db9349b504784b95690844ee10f43769cbdd`.

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
| 1k/10k/100k construction | `pass` (clean-commit development evidence) | Exact synthetic Assets, Wiki/Graph-derived rebuild, no whole-Vault full scan, file/Canvas/provider-byte measurements bound to the implementation commit. |
| 10,000-request RSS / 8 readers | `pass` on local Darwin criterion | 10,000/10,000 successful MCP queries, 8.608871% observed current-RSS growth against the frozen 10% bound, unchanged Canonical Ledger, eight independent readers. This is not peak-RSS or cross-platform evidence. |
| Retrieval context utility | `partial` | Repository-visible development ablation exists; dense/graph, blind Human Gold, real model answer delta, and competitive comparators remain absent. |
| Legal retrieval | `partial` | Local evidence/Authority/temporal/OCR regressions execute; exact signed external 28-source Pack and independent critical-token review are `not_executed`. |
| Real Codex x3 | `not_executed` | Repository-external frozen corpus, Human Gold, exact isolated Host credentials, and final candidate were unavailable. |
| OpenCode/DeepSeek x3 | `blocked_not_executed` | Prior exposed key must be revoked; no new owner-only repository-external evaluation key was supplied. No real call was attempted. |
| 3 OS x Python 3.11/3.12/3.13 | `partial` | Darwin Python 3.11/3.12/3.13 each passed 1,147 tests with 9 explicit skips; Linux/Windows are `not_executed`. |
| Reproducible artifact construction | `pass` for local source candidate | Two builds were byte-identical; wheel, sdist, SBOM and license inventory were generated externally and hash-bound. |
| Public release lifecycle | `not_executed` | No release version/tag/public artifact exists; formal provenance/signing and public redownload remain unavailable. |

## Exact local evidence

| Evidence | Result / binding |
| --- | --- |
| Query/Graph 5,001 and 10,000 | File SHA `ad16d230360610e40037808ad9efdd75ccd5b8b02eda7f51bec15c0a753c185a`; all 7 exact targets selected. |
| Query 100,000 | File SHA `ec362bb5d57c4b702668d0a5f4098996ad8f88746f455e80a47393fb3cb6b1eb`; all 4 exact targets selected; derived rebuild executed. |
| 1k/10k/100k construction | File SHA `e905c2c228b78abcdb917018316d2b07adc8c708050da0e7c3bda9a1eb36830a`; 47 operations executed, 15 frozen-threshold passes, 0 failures. |
| 10,000 requests / 8 readers | File SHA `0430b3fb31a7377d7d48b1525aa48972ec4e70ed839679b2fc54e1f46799268e`; 10,000 successes, 0 failures, Ledger 9→9. |
| Legal local regressions | 86 passed; exact signed 28-source gate still absent. |
| Wiki network regressions | 68 passed, 3 explicitly skipped. |
| Retrieval/context regressions | 32 passed; development ablation only. |
| Persistent runtime/cache regressions | 18 passed. |
| Darwin Python 3.11/3.12/3.13 | Each 1,147 passed, 9 skipped; JUnit hashes are in `V0_13_PLATFORM_ARTIFACT_QUALIFICATION_REPORT.md`. |
| Reproducible package | Report SHA `40f4fd4b2cb077732eb1d82e56919cc8f5f23786646db17015cf2b99339e92fb`; wheel SHA `5d55867e13f5e9fb212591eda67d6f36357db2a84c44c2722fd11665a9d17206`. |

Detailed exact commands, metrics and limitations are recorded in the focused reports rather than
being expanded into a release claim here.

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
6. Linux and Windows across Python 3.11/3.12/3.13, plus resolution of every release-mandatory
   skip; the local Darwin matrix alone is insufficient.
7. Reproducible release artifacts, SBOM, provenance, signatures as applicable, and public
   redownload verification.
8. Cross-host OpenCode/DeepSeek runs only after Owner revocation of the old key and provision of a
   new owner-only evaluation secret outside the repository.

## Release decision

The locally reproduced P0 defects have minimal reviewed fixes, clean-commit scale evidence and
local regression coverage. That does not make the source candidate evidence-complete for release.
Sol therefore keeps package version `0.12.0`, creates no v0.13 tag or release, and records the
candidate as **not released**.

No claim of perfection, comprehensive leadership, SOTA, production readiness, legal correctness,
published RC, or GA is made.
