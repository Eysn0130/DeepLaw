# DeepLaw comparative evaluation and independent replication

Status: **Optional v0.10 comparative track**. This document does not define the core release or
quality gate; see [`EVALUATION_PROTOCOL.md`](EVALUATION_PROTOCOL.md).

## Corrected decision

DeepLaw does not seek or require certification by an outside institution. The earlier protocol
incorrectly made two secret holdouts and signatures from two organizations prerequisites for a
software quality claim. That design had four defects:

- unavailable external actors could block an otherwise reproducible local release;
- a signature proves possession of a key, not evaluator independence or correctness;
- secret data prevents owner-controlled public reproduction;
- external status could be mistaken for product, source, or legal Authority.

DeepLaw Evaluation Protocol v1 now supplies the core public, fixed, time-frozen, automatic, and
auditable quality gate. Independent parties may reproduce it without becoming an Authority source.

Comparative superiority remains a different empirical question. The strongest permitted statement
has a finite scope:

> DeepLaw `<version>` was compared with the listed systems under the listed frozen corpus, model,
> budget, hardware, and scoring protocol, and achieved the reported per-dimension results and
> confidence intervals.

No protocol can prove “better than every knowledge base,” including systems that did not
participate, are not public, or do not share the same task boundary.

## Evidence levels

| Level | Evidence | Permitted interpretation |
| --- | --- | --- |
| Q | exact release wheel passes Evaluation Protocol v1 | reproducible DeepLaw core quality result |
| C1 | public same-condition named-baseline run | result for the named systems and frozen workload |
| C2 | real Codex, Claude Code, and OpenCode model tasks | host-level task evidence for the named tasks |
| C3 | independent reproduction of Q, C1, or C2 | additional provenance for that exact result |

Q is the release quality gate. C1 and C2 are required for comparative claims. C3 is welcome but
optional. None of the levels grants legal Authority or proves a universal ranking.

## Same-condition comparative contract

The registry and tools under [`benchmarks/baselines`](../benchmarks/baselines) require every
registered operating point to use:

1. exact corpus, query, case-inventory, and split hashes;
2. the same reader model, prompt, decoding, tool policy, and context-token budget;
3. the same top-k and full provider-visible payload accounting;
4. fixed hardware, software, tokenizer, network, and measurement profiles;
5. exact baseline commit, dependency/model inventory, wrapper, and command;
6. raw outputs, stdout, stderr, resource records, and every failure/timeout/abstention;
7. build cost amortized over the same registered query count;
8. paired per-case statistics and confidence intervals.

The registered set is not a pool from which favorable systems may be selected after execution. A
comparative report must either include every preregistered system or clearly publish the unavailable
systems and remain ineligible.

Manual products such as Obsidian use the two-stage
[`manual_adapter.py`](../benchmarks/baselines/manual_adapter.py). The evaluator freezes the workflow
and input state before operation, then seals per-case results, screen recording, resource/failure
records, and pre/post vault archives. Manual execution is not rewritten as a fictitious command.

The collection gate verifies artifact structure, hashes, shared conditions, and completeness. It
does not enforce an OS network sandbox and does not judge whether an organization is independent.
Those facts must remain explicit evaluator records.

## Required dimensions

A comparative result reports at least:

- task success;
- useful-context recall and irrelevant-context rate;
- provenance coverage;
- wrong-version and invalid-authority admission;
- temporal update and contradiction handling;
- forgetting accuracy;
- poisoning and unauthorized-mutation success rates;
- isolation and abstention;
- cold/warm latency;
- build/index time, peak memory, disk, model/token usage, and amortized cost.

Wrong official version, unverifiable provenance, Authority confusion, private-data disclosure,
poisoning, and unauthorized mutation are hard failures. They cannot be averaged away by retrieval
or task-success scores.

Primary comparative metrics use paired per-case uncertainty. The existing historical protocol uses
10,000 bootstrap samples, 95% intervals, and Holm–Bonferroni correction. A new comparative
preregistration may choose a different statistically justified method only before seeing results
and must version the protocol.

## Real host-model track

Lifecycle checks already prove that Codex, Claude Code, and OpenCode adapters can be installed and
invoked without a model call. They are deliberately marked:

```text
model_task_acceptance=false
model_task_results_claimed=false
```

A real task collection must additionally bind host and model versions, prompt, enabled plugins and
tools, granted capabilities, network policy, input/output digests, Knowledge Capsule, token/cost
record, outcome rubric, and failure reason. A real Codex run cannot substitute for missing Claude
Code or OpenCode evidence, and a generated transcript cannot substitute for a host receipt.

## Optional private or independent evaluation

An evaluator may choose a private holdout or detached Ed25519 signature. If so:

- commit the dataset bytes or publish a timestamped commitment before receiving the candidate;
- bind exact candidate wheel/container, commit, configuration, and output bytes;
- prevent result-dependent tuning;
- use an externally obtained trusted public key when signature identity matters;
- state who controls labels and which parties saw them;
- never promote signed content to official or legal Authority.

The historical external tooling under [`benchmarks/external`](../benchmarks/external) is retained
for this optional path and for audit reproducibility. Its v1-v3 protocol files keep their original
meaning and must not be relabeled as current v0.10 results. Placeholder example manifests are not
evidence.

## Current state

For v0.10:

- Evaluation Protocol v1 is implemented and wired into the exact-wheel release gate;
- the named-baseline registry and collection machinery are available;
- no complete same-condition v0.10 named-baseline collection is recorded;
- no real three-host model-task collection is recorded;
- no paired comparative confidence report or complete comparative cost/failure inventory exists.

Therefore `quality_protocol_eligible` can be true for the exact release while
`competitive_claim_eligible=false` remains mandatory.

## External evaluator checklist

- [ ] Candidate version, commit, tree, wheel/container, lock, and contract inventory match.
- [ ] Corpus, queries, labels, case inventory, environment, models, and budgets were frozen first.
- [ ] Every registered system ran or is explicitly reported unavailable.
- [ ] Raw output, per-case score, resource/cost record, and complete failures are present.
- [ ] Hard failures are reported separately and not averaged away.
- [ ] Paired uncertainty and preregistered multiple-comparison handling were executed.
- [ ] Real host runs bind authentic host receipts rather than generated transcripts.
- [ ] Private data, credentials, paths, and signing secrets are absent.
- [ ] Claims name only the systems, workloads, and versions actually evaluated.
