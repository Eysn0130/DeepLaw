# DeepLaw comparative evaluation and independent replication

Status: **Optional comparative track; current v0.13 qualification boundary**, reviewed 2026-08-17.
This document does not define the core release or quality gate; see
[`EVALUATION_PROTOCOL.md`](EVALUATION_PROTOCOL.md). Package/main remain `0.12.0 Beta`; the active
profile is `machine_evaluated_no_human_attestation`, status `machine_evaluation_pending`, and Gate
classification v8. No comparative or qualification result is implied by this protocol.

The current Provider advertisement is `knowledge-support` input v7/output v6 with only `query`,
`context`, and `explain`; input v1-v6 and output v1-v5 are internal compatibility. The shared
Context Compiler and three product roles (Task Continuity / Governed Project Knowledge,
Source-native Evidence Library, Living Wiki) are evaluated through the same bounded, source-bound
Capsule. Transcript, prompt, raw log and hidden reasoning are not automatically persisted.

## Current machine-only qualification boundary

The active machine profile requires the candidate Host, reference freezer, scorer A, scorer B and
arbiter to run in separate OS-enforced security domains with no shared filesystem, IPC or
transcript. Candidate
processes cannot read references or scorer outputs; scorers receive only sanitized candidate output
and sealed reference inputs. Credential brokers deliver Secrets only to the exact Host process; the
DeepLaw runner, scorers and arbiter receive neither the Secret nor its `.env`. Retained evidence must bind
executable/process/mount/ACL/network/IPC policy and negative canaries. Machine reviewers may produce
machine audit evidence, but it is never Human Gold, legal-expert attestation or `human_verified`.
Missing inputs or failed isolation remain `not_executed`/blocked and cannot be converted to a pass.

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

## Continuity Pass 2 disposition (development evidence only)

Pass 2 follows the retained **Pass 1** implementation boundary. Pass 1 Gold/protocol inputs and
local reports remain historical. The continuity correction is commit
`2f31bff4069e6cf01edf017134e5a760becb5360`, and the semantic release-evidence correction is commit
`d7da1869287fd590d820f7dd60506abdcb826ad4`. This tracked protocol cannot bind its own final tree;
no qualification wheel or external report hash is recorded. The three reproduced kernel defects
have these bounded repairs:

- an exact task-route hit is an independent bounded reservation before ordinary selection; the
  no-route ceiling remains `512`, one reserved route slot leaves at most `511` ordinary
  candidates, and the combined/global budget is unchanged;
- retrieval uses `task + goal`, while the route digest is generated only from canonical task text
  inside the domain; and
- one route has one current checkpoint head: the first write creates one Knowledge Object, later
  writes create a new revision with `expected_revision` CAS, stale/concurrent writes return
  `checkpoint_head_conflict`, and a pre-fix multi-head read returns only a sanitized Gap for Owner
  `forget`/withdraw plus projection-rebuild reconciliation. LWW is forbidden.

The route projection is derived/rebuildable; the continuity correction adds no canonical
Knowledge table, migration, or sink schema, and `knowledge-sink.input/v2` bytes are unchanged.
These are kernel observations, not Q, C1, C2, or C3 evidence. Core gates are not lowered.
Capability gates may remain `not_claimed` when not
declared (Timeline, semantic restore, and Claude/OpenCode); the Competitive Claim gate is
independent and cannot be satisfied by local kernel evidence. For the affected PRD rows:
`kernel=Implemented`, `E2E=Target`, `external qualification=not_executed`.

Pass 2 also closes the v0.13 release-evidence semantic boundary. The closed
`commercial-evidence-report/v1` records observations and content hashes but no `passed` or release
decision. The closed `v013-release-gate-classification/v1` freezes gate categories, minimum runs,
model requirements, metric bounds, and exact hard-zero counter inventories. A deterministic
validator reads those bytes and rejects weakened thresholds, missing counters, stale candidate or
protocol bindings, development-as-blind claims, secret canaries, and private absolute paths. Only
`v013_commercial_release` may derive manifest v6; `release_policy` then validates the envelope and
asset invariants. Publish and public-redownload paths rerun the semantic validator first. No such
report or manifest was generated for this candidate, so every external gate remains
`not_executed`.

### Explicit skip disposition

The following nine lanes are recorded as non-results and remain required where marked; a skip is
never a pass:

| Lane | Disposition |
|---|---|
| Statement scale 10k | `required not_executed` |
| Statement scale 100k | `required not_executed` |
| Relation truncation 500/5000 | `required not_executed` |
| Wiki wrong merge | `required not_executed` |
| Wiki alias collision | `required not_executed` |
| Wiki cycle | `required not_executed` |
| Historical v0.6 wheel | `separate compatibility not_executed` |
| Windows native ACL | `macOS not_applicable`; Windows evidence remains required |
| Windows native junction | `macOS not_applicable`; Windows evidence remains required |

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
meaning and must not be relabeled as current v0.11 results. Placeholder example manifests are not
evidence.

## Current state

For v0.11:

- Evaluation Protocol v1 is implemented and wired into the exact-wheel release gate;
- the named-baseline registry and collection machinery are available;
- no complete same-condition v0.11 named-baseline collection is recorded;
- no real three-host model-task collection is recorded;
- no paired comparative confidence report or complete comparative cost/failure inventory exists.

Therefore `quality_protocol_eligible` can be true for the exact release while
`competitive_claim_eligible=false` remains mandatory.

For the current Continuity Pass 2 source candidate, the disposition remains
`source_candidate_remains_not_released` with `package_version=0.12.0`,
`release_gate_passed=false`, `claim_eligible=false`, and
`competitive_claim_eligible=false`. Real Gold, Legal Pack, Host, scale, three-OS, and supply-chain
evidence remain `not_executed`; this protocol records no final artifact hash for Pass 2.

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
