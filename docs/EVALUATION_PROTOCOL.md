# DeepLaw Evaluation Protocol

Status: **Current v1 contract for software v0.11.0**, frozen on 2026-07-30.

## Decision

DeepLaw does not require certification by an external institution. Its core quality gate is a
public, reproducible, self-verifying protocol made of:

1. a public benchmark and machine-readable schemas;
2. scoring rules fixed before the release candidate;
3. a maintainer-visible, time-frozen holdout;
4. one offline runner that emits every component result;
5. a complete report, functional scoring digest, and checksum inventory;
6. an independent verification mode that rejects changed or missing bytes.

Independent replication remains useful, but it neither creates product Authority nor blocks a
release. Comparative superiority is a separate claim: it still requires actual same-condition
named-baseline runs, real host-model tasks, paired confidence intervals, and complete failure and
cost records.

## Canonical artifacts

| Role | Path |
| --- | --- |
| Protocol | `benchmarks/evaluation/protocol-v1.json` |
| Public temporal holdout | `benchmarks/evaluation/repository-temporal-holdout-v1.json` |
| Autonomy and security suite | `benchmarks/evaluation/autonomy-safety-v1.json` |
| Typed Compiler gold suite | `benchmarks/evaluation/typed-compiler-gold-v1.json` |
| Runner and verifier | `benchmarks/evaluation/run_protocol.py` |
| Protocol schema | `contracts/evaluation-protocol.v1.schema.json` |
| Summary schema | `contracts/evaluation-report.v1.schema.json` |

The protocol, labels, thresholds, runner, component schemas, and holdout hashes are freeze paths.
For a release-bound result, the candidate commit must be a strict descendant of the most recent
commit that changed any freeze path. The candidate must use a clean worktree and the exact wheel
whose SHA-256 enters both the evaluation report and release manifest. Editing a freeze path starts
a new freeze; a report cannot silently reuse the old temporal boundary.

The holdout is public and its labels are visible to maintainers. The report therefore always fixes:

```text
public_holdout=true
labels_visible=true
secret=false
contamination_claim_eligible=false
```

Calling this dataset secret, unseen, blind, or contamination-free is a protocol violation.

## Fixed score

The v1 overall score is:

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

Scores cannot average away hard failures. Any forbidden version/authority admission, autonomy suite
gate failure, unauthorized mutation, authority elevation, persistent injection admission,
restricted disclosure, Typed Compiler hallucination, or unsupported claim fails the protocol.
Latency is reported by component suites but excluded from the cross-machine functional digest.

The Typed Compiler component tests deterministic, source-bound extraction. It does not stand in for
model-generated cross-document synthesis. The core report also does not execute real Codex, Claude
Code, or OpenCode model sessions and does not execute a competing product.

## Run and verify

Source-tree runs are useful for development but cannot set `quality_protocol_eligible=true`.

```bash
uv run python -m benchmarks.evaluation.run_protocol \
  --repository . \
  --output-dir /tmp/deeplaw-evaluation

uv run python -m benchmarks.evaluation.run_protocol \
  --repository . \
  --verify-report-dir /tmp/deeplaw-evaluation
```

The formal release job installs the exact candidate wheel into an isolated environment and uses:

```bash
python -m benchmarks.evaluation.run_protocol \
  --repository . \
  --candidate-wheel /path/to/deeplaw-0.11.0-py3-none-any.whl \
  --output-dir /path/to/evaluation \
  --require-eligible

python -m benchmarks.evaluation.run_protocol \
  --repository . \
  --verify-report-dir /path/to/evaluation \
  --require-eligible
```

Passing `--candidate-wheel` is not a label-only assertion. Before any suite runs, the protocol
opens the wheel as a bounded ZIP, validates its path and metadata inventory, and compares every
installed `deeplaw/` package file byte-for-byte with that wheel. Editable-source, mixed-install,
symlinked, missing, extra, or hash-mismatched runtimes fail before a report can become eligible.

The output directory contains:

- `evaluation-report.json`;
- four complete component JSON reports;
- `EVALUATION_REPORT.md`;
- `SHA256SUMS`.

The release preserves these bytes, renaming the nested checksum file to
`EVALUATION_SHA256SUMS` only to keep all flattened GitHub release asset names unique. The root
release checksum inventory, Sigstore signing inputs, GitHub provenance, and release manifest cover
the evaluation artifacts.

## Claim policy

`quality_protocol_eligible=true` means only:

> The exact DeepLaw release wheel passed the published DeepLaw Evaluation Protocol v1 at its fixed
> thresholds, hard-failure rules, and public temporal freeze.

It does not mean:

- the public holdout was hidden or contamination-free;
- DeepLaw is better than an unexecuted system;
- a deterministic compiler suite proves model synthesis quality;
- no future workload can fail;
- an evaluator grants legal, official, or product Authority.

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
