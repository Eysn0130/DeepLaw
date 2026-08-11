# DeepLaw Evaluation Protocol

Status: **v1 historical boundary; v2 current repository-visible development protocol**.

## Decision

Protocol v1 remains immutable evidence for the v0.12 source boundary. Its frozen repository Gold
correctly rejects the current v0.13 source-candidate bytes; changing v1 hashes to hide that drift is
forbidden. Protocol v2 and repository Gold development v3 are the current default local-development
fixtures. They are public, label-visible, repository-visible, non-independent, and never release or
claim evidence.

The v0.13 release gate additionally requires repository-external Human Gold, Compiler/Evaluator
isolation receipts, frozen qualification/final-blind holdouts, passing real Host/model tasks, exact
Legal Pack evidence, complete current-candidate scale, and cross-platform results. Pass 11 retains
some claim-ineligible exact-candidate Host, editor, scale, and artifact observations, but they do
not supply this complete external gate set and are not part of this repository-visible development
protocol. See [`V0_13_PASS11_FINAL_DISPOSITION.md`](V0_13_PASS11_FINAL_DISPOSITION.md).

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

These outputs are reproducible local diagnostics, not the repository-external v0.13 Human Gold or
Host package. A v0.13 commercial manifest must bind separate external evidence and isolation
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
