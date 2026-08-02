# DeepLaw benchmarks and evidence

Status: **v0.12.0 current evaluation map**, 2026-07-30.

## One quality source of truth

The canonical quality gate is
[`DeepLaw Evaluation Protocol v1`](EVALUATION_PROTOCOL.md). It replaces the earlier design in
which a software release was blocked on secret datasets and signatures from outside institutions.
External certification is not required. The new protocol is public, time-frozen, executable,
release-artifact-bound, and independently verifiable from its emitted files.

The following decisions are intentionally separate:

| Decision | Current machine state | Meaning |
| --- | --- | --- |
| Release engineering | `commercial_release_eligible=true` | exact package bytes, three-OS tests, lifecycle, supply chain, docs, and security gates passed |
| Core quality protocol | `quality_protocol_eligible=true` for a clean exact release wheel | the fixed public protocol, component minima, overall minimum, and hard-failure rules passed |
| Comparative superiority | `competitive_claim_eligible=false` | actual named-system and real host-model comparative evidence is not complete |

The retained `commercial_release_eligible` field is a backward-compatible machine contract name.
It is not an external certification, legal status, or homepage positioning claim.

## Evaluation Protocol v1

Canonical inputs:

- [`protocol-v1.json`](../benchmarks/evaluation/protocol-v1.json);
- [`repository-temporal-holdout-v1.json`](../benchmarks/evaluation/repository-temporal-holdout-v1.json);
- [`autonomy-safety-v1.json`](../benchmarks/evaluation/autonomy-safety-v1.json);
- [`typed-compiler-gold-v1.json`](../benchmarks/evaluation/typed-compiler-gold-v1.json).

The protocol fixes four weighted components:

| Component | Weight | Minimum | Hard-failure examples |
| --- | ---: | ---: | --- |
| repository development | 0.10 | 0.75 | suite quality gate or forbidden admission |
| repository temporal holdout | 0.35 | 0.80 | wrong version/authority admission |
| autonomy and safety | 0.35 | 1.00 | unauthorized mutation, elevation, persistent injection, restricted disclosure |
| Typed Compiler quality | 0.20 | 1.00 | hallucinated or unsupported claim |

The weighted overall minimum is `0.85`. Every component minimum must pass and the hard-failure
list must be empty. The release report binds:

- exact candidate commit and tree;
- exact wheel name and SHA-256;
- the strict ancestor freeze commit;
- protocol and suite bytes;
- complete case-level component reports;
- a functional scoring digest;
- report and artifact checksums.

The holdout is deliberately public and maintainer-visible. Time freezing prevents a release
candidate from modifying the benchmark after seeing its current results, but it cannot prove
secrecy or absence of contamination. The runner encodes that boundary rather than relying on prose.

## v0.11 Living Wiki and Authoritative Pack gates

The source-free
[`quality-suite-v1.json`](../benchmarks/living_wiki/quality-suite-v1.json) is a separate first-party
CLI gate. Its runner executes governed source registration, fake-Agent compilation, ranked and
lifecycle queries, exact citation checks, withdrawal/restriction handling, context compilation,
Wiki/Canvas inspection, and destructive derived-state rebuild. It records Recall@K, Precision@K,
MRR, nDCG, citation/evidence binding, source coverage, compiled/fallback ratios, byte savings,
cold/warm latency, compilation/refresh/rebuild latency, peak RSS, recovery and zero-cost offline
execution. The deterministic labels are not presented as human semantic gold.

The release job first builds the exact `0.10.0` baseline commit
`42382b264f4297965c25aaac6e85619e9e0d49b7` with the frozen build environment; its
reproducible wheel SHA-256 is
`9bda60831e4380092c9a3bdb80103b5ec8abbf5a2be0adf6ffd57f61cfa46ca0`. Baseline and
candidate then run sequentially on the same runner with identical source bytes, suite, query
configuration, Python, SQLite, hardware identity, and network policy. The separately validated
comparison report rejects any functional metric decrease, any security failure, or latency beyond
the declared deterministic noise tolerance. All three JSON reports are release assets and are
bound by manifest v4.

The signed Legal Pack is assessed separately through the sanitized
[`v0.11-28-source-decision-matrix.json`](../benchmarks/quality/v0.11-28-source-decision-matrix.json).
That matrix binds all 28 exact catalog hashes, per-source parser/fragment/locator state, snapshot
and rollback evidence, rebuild reproducibility, and the same frozen 37-case legal evaluation before
and after the controlled reparse. It does not copy titles, source text, local paths, or private
payloads into the repository, and it does not merge `law_support` Authority with the Living Wiki
permission plane.

## v0.12 deterministic semantic and 28-source gates

The v0.12 semantic gate freezes 15 cases, fixture bytes, queries, scoring policy, and five executed
adversarial challenges. A deterministic no-model Agent uses the real governed Compilation
Packet/Plan/Receipt transaction; the first-party CLI then executes query, context, source fragment,
verification, actual cursor continuation, cold/warm reuse, and bounded Capsule checks. Entity and
Concept precision is target-scoped, with extraction completeness and source coverage reported
separately so correct extra objects are not scored as false positives.

Six isolated read-only machine-auditor roles must each confirm every applicable case and safety
gate for the exact commit/tree/Gold/fixture/Schema binding. There is no majority vote. The consensus
and bilingual Owner packets are derived artifacts and explicitly record
`human_gold_review.status=not_required`, `maintainer_confirmed=false`, and `reviewer_id=null`.
External real-model semantic execution remains `not_executed` and
`competitive_claim_eligible=false`.

The v0.12 28-source runner consumes the exact signed catalog, local immutable bytes, active
Authoritative Pack, frozen exact-identifier/citation cases, snapshot/restore evidence, and
first-party CLI/MCP results. It emits a sanitized per-source decision matrix without titles, text,
local paths, databases, or private identifiers. It measures target-scoped Recall/Precision, MRR,
nDCG, citation and claim binding, source/extraction coverage, current/historical admission,
repeated-query reuse, output bytes, latency, resources, and all declared zero-failure safety
counters. Authoritative evidence remains in the physically separate read-only `law_support` plane;
Agent interpretation cannot become legal Authority.
The current frozen set records 23 `no_action` decisions and 5 risk-driven
`reparse_source_ir` decisions. Those five retain explicit signed-policy parser review warnings;
fragment/locator completeness is therefore reported separately from the `23/28` parse-risk-free
rate.

## What is actually exercised

### Repository retrieval

The public development set and temporal holdout both run lexical, deterministic local dense, and
hybrid retrieval. Reports retain Hit@1, useful-context recall, irrelevant-context rate, forbidden
admissions, category results, per-case rankings, source inventory hashes, and elapsed time.

The quality score uses the hybrid operating point:

```text
(Hit@1 + useful-context recall + (1 - irrelevant-context rate)) / 3
```

Mode-specific results remain visible. A weak pure dense score cannot be hidden by describing the
hybrid result as if every retrieval mode achieved it.

### Autonomous Knowledge Core security

The autonomy suite invokes the actual domain services for:

- an authorized, audited mutation and idempotent replay;
- CJK recall;
- stale compare-and-swap rejection;
- missing, wrong-scope, and revoked grants;
- attempted authority elevation;
- persistent prompt-injection quarantine;
- restricted disclosure rejection;
- forgetting;
- hash-chained Ledger verification.

Every v1 case must pass. An average score cannot offset a mutation, authority, injection, or
disclosure failure.

### Typed Compiler

The v1 gold suite runs the shipped `deterministic-v2` compiler over source-bound bilingual input.
It computes precision, recall, F1, source-span correctness, hallucination, unsupported claim, and
duplicate claim rates. This proves the declared deterministic extraction contract only. It does
not claim model-generated cross-document synthesis quality.

The older
[`dev-fixture-v1.json`](../benchmarks/typed_compiler/dev-fixture-v1.json) remains a scorer
contract fixture; it is not used as a release quality result.

## Running the protocol

Development run:

```bash
uv run python -m benchmarks.evaluation.run_protocol \
  --repository . \
  --output-dir /tmp/deeplaw-evaluation

uv run python -m benchmarks.evaluation.run_protocol \
  --repository . \
  --verify-report-dir /tmp/deeplaw-evaluation
```

Only the release workflow may add `--candidate-wheel ... --require-eligible`; it runs from a clean
candidate commit that strictly postdates the freeze and installs the exact wheel in isolation.
Copying a source-tree report into release assets does not grant eligibility.

## Comparative track

The named-baseline registry and runners under [`benchmarks/baselines`](../benchmarks/baselines)
remain the canonical same-condition comparison kit. The registry currently covers 17 operating
points, including RAGFlow, Graphiti, PageIndex, Mem0, OpenKB/LLM Wiki-style systems, Obsidian, and
DeepLaw operating points. A complete comparison must preserve:

- one frozen corpus, query set, case inventory, and evaluator run;
- the same reader, prompt, context budget, top-k, hardware, network policy, and measurement rules;
- exact baseline repository commits, dependency/model inventories, commands or manual workflow;
- raw outputs, per-case scores, stdout/stderr, failures, build/query time, memory, disk, token, and
  monetary cost;
- paired confidence intervals and every preregistered loss, timeout, abstention, and hard failure.

Those runners validate evidence structure; they do not manufacture results. No complete v0.11
named-baseline collection or real Codex/Claude Code/OpenCode model-task collection exists in this
repository, so comparative claims remain closed.

[`EXTERNAL_BENCHMARK_PROTOCOL.md`](EXTERNAL_BENCHMARK_PROTOCOL.md) describes this optional
comparative and replication path. Its older secret-held-out and organization-signature machinery
is retained only for historical reproducibility and for evaluators who independently choose it.
It is not the core quality or release gate.

## Scale and engineering diagnostics

These reports remain useful but do not participate in Evaluation Protocol v1:

- [`retrieval-fabric-100k-2026-07-27.json`](../benchmarks/scale/retrieval-fabric-100k-2026-07-27.json)
  executes 100,000 source-bound assets, update, lineage, forgetting, cold integrity, and warm
  retrieval/Capsule paths.
- [`retrieval-fabric-1m-2026-07-28.json`](../benchmarks/scale/retrieval-fabric-1m-2026-07-28.json)
  executes the same class of workload over 1,000,000 assets.
- [`document-engine-actual-pdf-2026-07-28.json`](../benchmarks/release/document-engine-actual-pdf-2026-07-28.json)
  covers the fixed offline MinerU pipeline and exact local model bundle on one generated PDF.

They use developer-generated fixtures or non-clean historical trees and therefore remain
diagnostics, not natural-language quality, generalization, or superiority evidence.

## Historical evidence

Historical snapshots are intentionally immutable:

- v0.9 three-OS and release-engineering evidence;
- v0.7 named-baseline collection contracts and compatibility fixtures;
- v0.6 control-plane diagnostics;
- v0.5 external-protocol candidate manifests;
- v0.4 Legal Pack installation snapshots;
- v0.3 SQLite v5 diagnostic snapshots.

Their version, schema, `claim_eligible`, and limitation fields continue to mean what they meant
when recorded. A newer release must not rewrite an old report or relabel it as current evidence.

## Non-negotiable claim rules

- Discovery scores do not create Authority, legal validity, permission, or adjudication.
- Unit tests, synthetic demos, feature matrices, signatures, and self-description do not prove
  superiority.
- Hard failures for wrong official version, unverifiable provenance, authority confusion, private
  data disclosure, poisoning, or unauthorized mutation cannot be averaged away.
- A public holdout must not be described as secret, unseen, or contamination-free.
- “Better,” “leading,” and “SOTA” require named comparators, fixed conditions, actual results,
  uncertainty, costs, and failure samples. Until then,
  `competitive_claim_eligible=false`.
