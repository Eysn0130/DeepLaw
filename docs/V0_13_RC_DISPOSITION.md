# DeepLaw v0.13 source-candidate disposition

Decision: **superseded by Core Scope Freeze; local core qualification is partial; not released**.

DeepLaw remains package version `0.12.0`. No v0.13 tag, RC, GA, catalog, public release, or
published artifact is authorized. `release_gate_passed=false`, `claim_eligible=false`, and
`competitive_claim_eligible=false`.

The later Core Scope Freeze is authoritative for the current working candidate. Continuity missed
its frozen context-efficiency threshold; the Evidence Wiki development task passed with a deferred
Statement resolver; and the legal exact-evidence development task failed to admit current primary
evidence from an unsigned Pack. See `V0_13_CORE_SCOPE_DISPOSITION.md`. Any language below saying
that local requalification is complete is historical evidence for commit `ee06bb3`, not the
current disposition.

This document records the release-safe disposition after the 2026-08-08 qualification and
remediation pass. The implementation candidate is commit
`ee06bb3ef9989c671638deda95968690d628f8ca`, tree
`5fe2895a7c50f496a23612969844cd390b3cafad`.

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
- Python, both CLI Context commands and autonomous MCP Context still defaulted to Query Plan v5
  despite the prior qualification report. They now share one v6 domain assembler, additive local
  Capsule v3 and Provider v2 projection; v5/Capsule v2 remains explicit compatibility only.
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
- `recent_changes` incorrectly returned current-object browse results. It now reads only verified,
  registered event index/shards with bounded admission, integrity and truncation metadata; complete
  history pagination, object diff and `as_of` Wiki remain unimplemented.

The exact root causes, impact, failure witnesses, Task Cards, and focused commands are in
`V0_13_P0_REMEDIATION_REPORT.md` and `work-plans/V0_13_AGENT_LEDGER.md`.

## Local qualification disposition

| Area | Status | Evidence boundary |
|---|---|---|
| Credential and Host isolation | `pass` (local deterministic) | Ambient canary is absent from Host/MCP child, argv, prompt, output, report, and artifact; required PATH/locale/temp variables remain available. No real Provider credential was used. |
| Query v6 Statement tail recall | `pass` (development) | Exact 5,001/10,000/100,000 synthetic positions, randomized identities, bounded candidate pool, hard provider limit, persistent runtime. |
| Query/Context v6 controls and compatibility | `pass` (local contract) | Python, both CLI paths and autonomous MCP default to v6; closed Schema/Receipt behavior plus explicit v5/Capsule v2 compatibility; invalid inputs fail rather than disappear. |
| Graph smoke and predicate parity | `pass` (smoke only) | `graph_hops=0/1/2`, hub/deep/cycle/contradiction/temporal/self-loop/dangling behavior and exact predicate parity. |
| 10k/100k Relation scale and 500/5,000 truncation | `not_executed` | No safe audited bulk Relation constructor exists; the 120 mutations/minute owner grant was not weakened for a benchmark. |
| Query Trace | `pass` for process-local contract | TTL, entry/byte bounds, rotation, redaction, integrity hash, identity invalidation, close/restart deletion; durable cross-process Trace is not implemented. |
| Wiki network | `partial` | Local deterministic 12-kind, Registry, Link Index, Resolver, ownership/recovery and Recent Changes regressions execute. Relation Path, Guides/Codemap integration, complete history/object diff/`as_of`, identity Gold and current 10k/100k Wiki requalification remain absent. |
| 1k/10k/100k construction | `not_executed` (current continuation) | Prior development reports remain historical evidence; they were not rerun after the Context/Recent Changes remediation and are not promoted into the current release gate. |
| 10,000-request RSS / 8 readers | `not_executed` (current continuation) | Prior Darwin development evidence remains recorded, but no post-remediation RSS run or peak-RSS/cross-platform evidence exists. |
| Retrieval context utility | `partial` | Repository-visible development ablation exists; dense/graph, blind Human Gold, real model answer delta, and competitive comparators remain absent. |
| Legal retrieval | `partial` | Local evidence/Authority/temporal/OCR regressions execute; exact signed external 28-source Pack and independent critical-token review are `not_executed`. |
| Real Codex x3 | `not_executed` | Repository-external frozen corpus, Human Gold, exact isolated Host credentials, and final candidate were unavailable. |
| OpenCode/DeepSeek x3 | `blocked_not_executed` | Prior exposed key must be revoked; no new owner-only repository-external evaluation key was supplied. No real call was attempted. |
| 3 OS x Python 3.11/3.12/3.13 | `not_executed` (current continuation) | Prior Darwin matrix evidence is historical; the current implementation was not rerun across the matrix, and Linux/Windows remain `not_executed`. |
| Reproducible artifact construction | `not_executed` (current continuation) | Prior local package evidence does not bind the current implementation commit; fresh wheel/sdist/SBOM/provenance/public redownload remain required. |
| Public release lifecycle | `not_executed` | No release version/tag/public artifact exists; formal provenance/signing and public redownload remain unavailable. |

## Exact local evidence

| Evidence | Result / binding |
| --- | --- |
| Query/Graph 5,001 and 10,000 | File SHA `70f5d551a4bdcc9cbcf1a2210652577068afa9bf8168eae40b002757b2c3e424`; all 7 exact Statement targets selected; Relation/truncation lanes `not_executed`. |
| Query 100,000 | File SHA `e69d2f6eb7115db45a56137d224a2320b3f7633b06cae86185fe9248fa3bca5f`; all 4 exact Statement targets selected and derived rebuild executed; Relation/truncation lane `not_executed`. |
| 1k/10k/100k construction | Historical prior-candidate evidence only; not rerun in this continuation. |
| 10,000 requests / 8 readers | Historical prior-candidate evidence only; not rerun in this continuation. |
| Legal local regressions | Historical prior-candidate evidence only; exact signed 28-source gate still absent. |
| Wiki network regressions | Current focused combination: 100 passed, 3 explicitly skipped. |
| Context/query/source/trace/runtime/sink regressions | Current focused combination: 195 passed; development/contract evidence only. |
| Repository development Gold/protocol regressions | 10 passed; this is repository-visible development data, not repository-external Human Gold. |
| Full frozen repository suite | `uv lock --check` passed; `uv run --frozen pytest --strict-markers` produced 1,165 passed and 9 explicit skips. |
| Darwin Python 3.11/3.12/3.13 | Historical prior-candidate evidence only; current matrix not rerun. |
| Reproducible package | Historical prior-candidate evidence only; no current wheel hash or public redownload. |

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
