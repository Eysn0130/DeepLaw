# DeepLaw Evaluation Protocol

Status: **v3 Kernel-release protocol; package/main 0.12.0 Beta**.
Reviewed 2026-08-21. Active qualification is
[`benchmarks/v013/active-qualification-v3.json`](../benchmarks/v013/active-qualification-v3.json)
with `status=machine_evaluation_pending`, profile
`kernel_release_core`, and Gate classification v9. The 13 v9 Kernel Release Core gates remain
required; `release_ready=false`, `claim_eligible=false`, and no `0.13.0` tag/release follows from
this document.

The current Provider advertisement is `knowledge-support` input v7/output v6 and exposes only
`query`, `context`, and `explain`. Input v1-v6 and output v1-v5 remain internal compatibility
contracts. Evaluation never treats transcript, prompt, raw log, hidden reasoning, Secret, local
path, or unadmitted content as Provider evidence. Task Continuity uses explicit task and Host
session seams; automatic transcript memory is out of scope.

## Decision

Protocol v1 remains immutable evidence for the v0.12 source boundary. Its frozen repository Gold
correctly rejects the current v0.13 source-candidate bytes; changing v1 hashes to hide that drift is
forbidden. Protocol v2 and repository Gold development v3 are the current default local-development
fixtures. They are public, label-visible, repository-visible, non-independent, and never release or
claim evidence.

The v0.13 machine-only Kernel release gate additionally requires OS-enforced Host/Secret-isolation
receipts, passing exact-artifact real Host/model tasks, professional-source evidence, the exact
10,000-object support boundary, and cross-platform/supply-chain results. Official signed Legal Pack
evidence, semantic restore, Claude, and GUI/Desktop evidence gate only those Capability claims.
Blind comparative holdouts, machine-reference isolation, panels, scorers, arbitration, and
comparative results gate only Competitive/Research claims. Pass 11 retains
some claim-ineligible exact-candidate Host, editor, scale, and artifact observations, but they do
not supply the complete v9 Core gate set and are not part of this repository-visible development
protocol. See [`V0_13_PASS11_FINAL_DISPOSITION.md`](V0_13_PASS11_FINAL_DISPOSITION.md).

## Gate v9 release boundary

Kernel Release Core consists of `canonical_integrity`, `migration_recovery`,
`secret_host_isolation`, `bounded_context`, `source_citation_locator`, `living_wiki`,
`scale_performance`, `supported_platforms`, `reproducible_supply_chain`, `codex`, `opencode`,
`selective_forget`, and `timeline`. Every one must be recomputed as `passed` from retained evidence
for the exact candidate wheel/sdist before `release_ready=true` is mechanically possible.

Capability and Competitive/Research entries are always retained in the result inventory. An
unexecuted entry is `not_executed` with its claim flag false; it is never silently omitted or
converted to a Core failure. Generic professional-source evidence must cover original bytes/hash,
Document/Version/Fragment/Locator, wrong-version rejection, effective date, exception/proviso and
cross-reference duties, false-Authority hard failure, OCR critical-token Gap, and Wiki-to-exact-
Source drill-down. This does not establish official Legal Pack status, Human/legal-expert
attestation, `human_verified`, legal applicability, or a verdict.

DeepLaw's evaluation architecture uses:

1. a public benchmark and machine-readable schemas;
2. scoring rules fixed before the release candidate;
3. explicitly labelled development, qualification, and final-blind data boundaries;
4. one offline runner that emits every component result;
5. a complete report, functional scoring digest, and checksum inventory;
6. an independent verification mode that rejects changed or missing bytes.

Independent replication neither creates product Authority nor replaces the v0.13 commercial gate.
Comparative superiority is a separate claim requiring actual same-condition named-baseline runs,
real Host-model tasks, paired uncertainty, and complete failure and cost records.

## Canonical artifacts

| Role | Path |
| --- | --- |
| Current development protocol | `benchmarks/evaluation/protocol-v2.json` |
| Historical protocol | `benchmarks/evaluation/protocol-v1.json` |
| Current repository development Gold | `benchmarks/quality/repository-gold-development-v3.json` |
| Historical repository Gold | `benchmarks/quality/repository-gold-v1.json` |
| Public temporal holdout | `benchmarks/evaluation/repository-temporal-holdout-v1.json` |
| Autonomy and security suite | `benchmarks/evaluation/autonomy-safety-v1.json` |
| Typed Compiler gold suite | `benchmarks/evaluation/typed-compiler-gold-v1.json` |
| Runner and verifier | `benchmarks/evaluation/run_protocol.py` |
| Current protocol schema | `contracts/evaluation-protocol.v2.schema.json` |
| Current summary schema | `contracts/evaluation-report.v2.schema.json` |
| Historical protocol/report schemas | `contracts/evaluation-protocol.v1.schema.json`, `contracts/evaluation-report.v1.schema.json` |

The protocol, labels, thresholds, runner, component schemas, and corpus hashes are freeze paths.
A candidate must be a strict descendant of the newest commit changing its selected freeze paths.
Editing a freeze path starts a new development freeze; it never converts visible labels into an
external holdout. A release-bound candidate must also use a clean worktree and the exact wheel
whose SHA-256 enters both the external evaluation package and commercial manifest.

The v2 corpus is visible to the candidate authors. Every v2 report therefore fixes or derives the
equivalent of:

```text
public_holdout=true
labels_visible=true
secret=false
contamination_claim_eligible=false
external_human_gold=false
independent_evaluator=false
quality_protocol_eligible=false
```

Calling it external, independent, secret, unseen, blind, contamination-free, qualification, or
final evidence is a protocol violation. Supplying a wheel or clean commit cannot change these
facts.

## Historical v1 score and current v2 purpose

The immutable v1 overall score remains:

```text
0.10 × repository_development
+ 0.35 × repository_temporal_holdout
+ 0.35 × autonomy_safety
+ 0.20 × typed_compiler_quality
```

| Component | Minimum | What is actually executed |
| --- | ---: | --- |
| Repository development | 0.75 | lexical, local dense, and hybrid retrieval over the public development set |
| Repository temporal holdout | 0.80 | version- and authority-sensitive retrieval over source bytes frozen before the candidate |
| Autonomy and safety | 1.00 | authorized write, idempotency, CAS rejection, grant/scope/authority/injection/disclosure/forget/revocation and Ledger verification |
| Typed Compiler quality | 1.00 | deterministic bilingual typed-section extraction against source-bound gold claims |
| Weighted overall | 0.85 | fixed weighted sum after all component minima pass |

Scores cannot average away hard failures. Any forbidden version/Authority admission, autonomy suite
gate failure, unauthorized mutation, authority elevation, persistent injection admission,
restricted disclosure, Typed Compiler hallucination, or unsupported claim fails the protocol.
Latency is reported by component suites but excluded from the cross-machine functional digest.

Protocol v2 keeps these deterministic checks available for regression and rotates repository source
hashes through the explicit development-v3 fixture. Its report cannot set
`quality_protocol_eligible=true`, even when all component thresholds pass. The Typed Compiler
component does not stand in for model-generated cross-document synthesis; neither v1 nor v2 runs
real Codex, Claude Code, OpenCode, or a competing product.

### Pass 12 continuity evaluator v2

The Pass 12 continuity scorer is a separate, claim-ineligible evaluator design. Its canonical
development inputs are:

- `benchmarks/evaluator/continuity-qualification-gold-v2.json`;
- `benchmarks/evaluator/score_continuity_qualification_v2.py`;
- `contracts/continuity-qualification-gold.v2.schema.json`;
- `contracts/continuity-human-review.v1.schema.json`.

Candidate prompts and fixtures may not contain the expected action, Gold/marker labels, or exact
expected/forbidden Statement IDs. Gold and candidate bytes are loaded separately, and an
independent bilingual Human review must bind the exact SHA-256 of both artifacts before the result
is scored. Missing, incomplete, non-independent, rejected, or digest-mismatched review fails
closed. The checked-in Gold remains `development_evaluator_only`, and no real Human review artifact
is fabricated by tests.

Machine correctness uses selected Statement IDs, a closed action, a closed release state, gap
codes, and explicitly frozen required-duty labels. Duty Coverage is computed per duty from each
duty's bound Statement IDs and Gap codes; it is not an alias for Statement recall. It reports First
Correct Action, Decision Preservation, Wrong-State Admission, Recall@K,
Precision@K, MRR, nDCG, Useful Context Recall, relevant/context characters, redundancy, duplicate
evidence, Duty Coverage, Gap Correctness, and Provider bytes. `context_chars` covers the complete
canonical Provider content, not only Statement text. `provider_bytes` and its SHA-256 are recomputed
from the exact canonical inner Provider Capsule and must match both the Host observation and
delivery metadata; receipt, Query Trace, route metadata, and local audit metadata are outside that
content. Natural-language summary substrings, English casing, and translation wording cannot
create a pass. Forbidden state and a Provider payload above 65,536 bytes remain hard failures.

Host-call scoring records first-call validity independently. One initial call plus at most one
safe, read-only, budget-bounded retry is allowed. Zero calls, more than two calls, any write or
wrong leaf, an invalid final Capsule, repeated large payloads, or aggregate payload overflow hard
fails. Exact-one-call is therefore observable but is not the sole success definition. Human review
remains mandatory and claim eligibility remains false even when all machine fields pass.

The v3 repository development corpus may rotate only exact source-byte hashes after an accepted
current-source correction. Its cases, labels, expected/forbidden IDs, thresholds, and governance
fields are not changed to fit candidate output. Historical v1 and retained Pass 11 evidence are
never rebound.

## Run and verify

The default runner selects protocol v2 and repository development Gold v3. These commands are local
development checks only:

```bash
uv run python -m benchmarks.evaluation.run_protocol \
  --repository . \
  --output-dir /tmp/deeplaw-evaluation

uv run python -m benchmarks.evaluation.run_protocol \
  --repository . \
  --verify-report-dir /tmp/deeplaw-evaluation
```

The v1 runner remains explicitly callable to verify the historical boundary. Against current
source bytes it is expected to reject the changed source hash:

```bash
uv run python -m benchmarks.evaluation.run_protocol \
  --repository . \
  --protocol benchmarks/evaluation/protocol-v1.json \
  --output-dir /tmp/deeplaw-evaluation-v1
```

Passing a candidate wheel is not a label-only assertion. Before any suite runs, the protocol
opens the wheel as a bounded ZIP, validates its path and metadata inventory, and compares every
installed `deeplaw/` package file byte-for-byte with that wheel. Editable-source, mixed-install,
symlinked, missing, extra, or hash-mismatched runtimes fail before a report can become eligible.

The output directory contains:

- `evaluation-report.json`;
- four complete component JSON reports;
- `EVALUATION_REPORT.md`;
- `SHA256SUMS`.

These outputs are reproducible local diagnostics, not the repository-external v0.13 machine
reference or Host package. A v0.13 commercial manifest must bind separate external evidence and isolation
receipts; development output cannot be renamed to satisfy that gate.

## Claim policy

Historical v1 `quality_protocol_eligible=true` meant only:

> The exact DeepLaw v0.12 release wheel passed Evaluation Protocol v1 at its fixed thresholds,
> hard-failure rules, and public temporal freeze.

It does not mean:

- the public holdout was hidden or contamination-free;
- DeepLaw is better than an unexecuted system;
- a deterministic compiler suite proves model synthesis quality;
- no future workload can fail;
- an evaluator grants legal, official, or product Authority.

Protocol v2 never makes that statement and always remains development-only.
`competitive_claim_eligible` remains `false` until the comparative track contains real results for
the predeclared named systems, real model-task receipts on all three hosts, paired uncertainty, and
the complete cost/failure inventory. No synthetic fixture, feature matrix, lifecycle smoke test, or
signature can substitute for those facts.

## Research basis

The protocol borrows mechanisms, not results or code:

- [LongMemEval](https://github.com/xiaowu0162/LongMemEval) motivates extraction,
  multi-session reasoning, update, temporal, and abstention coverage.
- [BEIR](https://github.com/beir-cellar/beir) motivates heterogeneous retrieval tasks,
  reproducible baselines, and explicit per-task metrics instead of one misleading average.
- [MTEB](https://github.com/embeddings-benchmark/mteb) motivates versioned task and benchmark
  definitions.
- [SWE-bench](https://github.com/SWE-bench/SWE-bench) motivates candidate-bound, executable,
  repository-level validation.
- [LiveBench](https://github.com/LiveBench/LiveBench) motivates objective scoring and
  time-based contamination controls.
- [OpenAI Evals](https://github.com/openai/evals), [HELM](https://github.com/stanford-crfm/helm),
  and [Inspect AI](https://github.com/UKGovernmentBEIS/inspect_ai) motivate versioned registries,
  transparent scenarios/metrics, and structured run logs.

No code, model, dataset, or new dependency from those projects is vendored by this change.
