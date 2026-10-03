# DeepLaw knowledge and continuity research, 2026-09-09

Status: **design evidence and delivery priorities; not qualification or a superiority claim**.
This dated review extends [the earlier upstream research](V0_13_UPSTREAM_RESEARCH.md). It does not
replace the [PRD](PRODUCT_REQUIREMENTS.md), [Architecture](ARCHITECTURE.md), subsystem contracts,
or frozen qualification inputs. Source inspection is not upstream execution or benchmark
reproduction. All comparative results mentioned below belong to their authors and tested settings.

## Decision

DeepLaw should compete on **correct evidence use and correct continuation as knowledge changes**.
The durable opportunity is a portable, revision-bound knowledge and work-state layer across Hosts.
More stored memories, larger graphs, more tools, or a claim of being stronger than RAG do not prove
that outcome. The existing shared kernel and three product roles remain the architecture.

The six directions below refine existing PRD outcomes, rather than add six new subsystems.
`Current` means an existing primitive or public contract; `Planned` means selected closure work;
`Target` means an unqualified research objective. None means `Released`.

| Direction and existing requirements | Existing foothold | Next falsifiable result |
| --- | --- | --- |
| Dependency-aware knowledge maintenance (`PRD-SRC-*`, `PRD-KNOW-005..010`, `PRD-OPS-001..004`) | Current revision/source bindings, Coordinator and rebuildable projections | Target: change one source, identify affected claims/relations/checkpoints, preserve independently supported knowledge, and refresh only affected dependents; compare incremental and full results and cost |
| Evidence-duty-aware progressive retrieval (`PRD-CTX-010..015`, `PRD-EVID-005..009`) | Current Query Plan, admission, bounded Capsule, Source/Wiki domain readers | Planned: a no-shell MCP client reaches exact admitted knowledge and source evidence. Target: selection explicitly covers or reports missing definitions, exceptions, provisos, references and temporal duties |
| Branch-safe portable work state (`PRD-CONT-001..014`) | Current Run/Checkpoint, route identity, expiry, CAS and separate Sink | Planned: one real Host saves and resumes a bounded checkpoint. Target: cross-Host continuation preserves accepted decisions and action status without transferring authentication or transcripts |
| Loss-aware context delivery (`PRD-PRINCIPLE-005`, `PRD-CTX-001..009`) | Current item/source/character/token/payload limits, Gap and revision joins | Target: selected, omitted, stale and unknown state can be distinguished under a fixed task budget; a Host can request additional admitted evidence without assuming compaction was lossless |
| Selective repair and forgetting (`PRD-PRINCIPLE-011`, `PRD-SEC-005..008`, `PRD-OPS-005..009`) | Current lifecycle, provenance, forget/rebuild and scope boundaries | Target: repair or forget a faulty input without preserving unsupported descendants, resurrecting it through caches/compaction, or removing unrelated useful knowledge |
| Outcome-tested accumulated knowledge (`PRD-KNOW-*`, PRD section 11) | Current typed procedural/reflective knowledge, observations and gaps | Target: a scoped, evidence-bound maintenance correction reduces a named recurring failure on later unseen tasks without acquiring instruction or execution authority |

The first direction is analogous to incremental compilation: source and knowledge revisions are
inputs, indexes and Wiki pages are replaceable outputs, and dependency changes determine necessary
work. It is not a claim that semantic dependency discovery is complete. Missing edges, independent
support, cycles and changes to a compiler/model must be measured explicitly. Cache validity requires
exact inputs and configuration; a fast stale cache is a failure.

The evidence-duty direction has two distinct questions: whether returned evidence is authentic and
whether it is sufficient for the task. Hashes and locators answer the first; they cannot establish
the second. A model may propose duties or follow-up queries; deterministic policy still decides
admission, version, source binding, bounds and commit. Unknown coverage remains a Gap. Source-first
legal duties do not grant DeepLaw responsibility for legal applicability or adjudication.

The work-state direction preserves goals, accepted decisions, constraints, verified facts, gaps,
next action and bounded artifact references. Retention duration is separate from memory category,
Authority and scope. No automatic expiry does not remove the owner's correction or deletion path.
An external action marked `unknown` requires Host-side verification before retry; DeepLaw must not
claim cross-system exactly-once execution or operate the tools itself.

Selective repair follows knowledge influence, not just filenames. A withdrawn input may invalidate
a derived conclusion while another conclusion remains independently supported. That requires a
governed revision/lifecycle change, not rewriting historical evidence or replaying irreversible
external actions. An error log or retrieved procedure remains untrusted data even after repeated
successful use.

## Primary research and what it actually establishes

- [Retrieval as Reasoning, v2](https://arxiv.org/html/2605.25480v2) studies compiled Wiki navigation
  and an Error Book on multi-hop QA. It motivates an inspectable search/read/check loop and scoped
  maintenance feedback. It does not qualify DeepLaw, Chinese legal reasoning, or production scale.
  The paper is a separate work from Microsoft's repository.
- [MemoryArena](https://arxiv.org/abs/2602.16313) evaluates interdependent multi-session tasks;
  its [project page](https://memoryarena.github.io/) explains the memory/action evaluation gap.
  Borrow task dependencies and environment-observed success, not recall alone. Its tasks do not
  replace DeepLaw's branch, Authority, privacy and exact-artifact acceptance requirements.
- [Supersede](https://arxiv.org/abs/2606.27472) isolates changing-fact maintenance under bounded
  memory. Reported scale experiments include a small sample and the training result is a single
  run. It motivates explicit stale-value errors; it does not show that all larger models or memory
  budgets are ineffective, or that training solves DeepLaw's governance problem.
- [TARL](https://arxiv.org/abs/2608.03699) distinguishes several memory-update actions and evaluates
  the resulting state, rather than only a binary write decision. Use that evaluation distinction
  when testing existing DeepLaw mutations. Model-selected reliability must not become legal
  Authority; the paper does not justify introducing parallel canonical Ledgers.
- [Dependency-guided rollback repair](https://arxiv.org/abs/2608.10502) studies recovery after a
  faulty memory has propagated. Its controlled and trajectory-derived evaluations motivate
  affected-descendant repair with benign-state preservation. This is not evidence that arbitrary
  missing provenance can be reconstructed or external effects can be undone.
- [MemSecBench](https://arxiv.org/abs/2607.27080) follows memory through write, use and forgetting
  in matched configurations. [MemoryGraft](https://arxiv.org/abs/2512.16962) studies poisoned
  experience reuse. They motivate lifecycle tests and counterexamples to treating retrieved
  experience as executable policy. Their reported attack rates are not DeepLaw measurements.

These papers are research evidence, including preprints. No result in this review was reproduced;
no retrieved instructions, private data, benchmark payloads or upstream runtimes were installed.

## Frozen implementation reading and reuse decisions

These are research anchors, not replacements for Gate v9's qualification coordinates. Licensing
must be checked for each file before any future copy; dependency admission additionally requires
offline, network, telemetry, supply-chain and rebuildability review.

| Upstream and exact commit | Inspected mechanism | Reuse decision |
| --- | --- | --- |
| [microsoft/llmwiki](https://github.com/microsoft/llmwiki/tree/b44df6ae95138d0edcbcc79b5b1d099c78bce5e0), MIT | `packages/core/src/ingest-context.ts`, ingest/lint and smart-ingest workflow: related existing pages and explicit next actions | Behavioral reuse in Compilation Packet/semantic inventory first; English keyword normalization removes Chinese text and must not be copied unchanged |
| [langchain-ai/openwiki](https://github.com/langchain-ai/openwiki/tree/b5d2407679825e10f86eb6ed159418563c9a8e45), MIT | `src/generation/run-state.ts`, page manifest, Wiki finalizer/link validation and `src/okf/claims-verification.ts` | Reuse source fingerprints, interrupted page jobs and producer-owned projection tests through the existing Coordinator; do not import its Agent/provider/auth/telemetry stack |
| [krishddd/llm-wiki](https://github.com/krishddd/llm-wiki/tree/690a47db1e97109f79daa4f211d401a1a9d71b9e), MIT | `llm_wiki/agentic_rag/sufficient_context.py` and maintenance design | Adapt covered/missing aspects and bounded follow-up queries; confidence/frequency do not activate Authority or resolve protected legal truth |
| [nvk/llm-wiki](https://github.com/nvk/llm-wiki/tree/7c94c9bf2968f17deb496b285db0afdb610a01d9), MIT | `claude-plugin/skills/wiki-manager/references/checkpoints.md` and session hook | Reuse scoped handoff, source identifiers and explicit omissions through DeepLaw checkpoints; do not import automatic transcript capture |
| [nowledge-co/community](https://github.com/nowledge-co/community/tree/eeb990a8faddf6b03bbb9c9ccfca416368d503d5) | `integrations.json`, `shared/behavioral-guidance.md` and Codex startup/stop integration | Reference-only: distinguish automatic, guided and manual delivery. No repository-wide license was established; this community integration repository is not evidence that the product core is open source |
| [getzep/graphiti](https://github.com/getzep/graphiti/tree/3ff5c160c57c6790aeb148e7388bf380ed351c7b), Apache-2.0 | `graphiti_core/utils/maintenance/edge_operations.py`: source episode attribution, valid/invalid time, candidate resolution and invalidation | Reuse temporal/conflict test patterns; model-proposed contradictory edges do not authorize invalidating legal sources. No new graph backend or second lifecycle engine |
| [Vrin-cloud/supersede](https://github.com/Vrin-cloud/supersede/tree/677993d3713c265329ac935262d3c08cbfa4cd63), Apache-2.0 | `src/supersede/temporal.py`, `reward.py`, `timeline.py`, `tests/test_temporal.py` | Reference-only evaluation ideas initially. Same subject/predicate with a different object is not universally supersession; multi-valued relations, authority and temporal boundaries require separate policy |

Two source-level cautions matter for direct reuse:

1. Supersede's `answer_matches` uses normalized substrings or token overlap, and stale-value
   penalty matches mentions. An answer containing the correct string in a denial, or citing an old
   value only to reject it, can be mis-scored. This follows from code inspection, not an executed
   benchmark. Such a scorer must not become a legal or release oracle. Exact typed state and
   action outcomes need independent scoring.
2. Graphiti's edge resolver uses model responses and temporal ordering to select invalidations.
   That is useful for evolving conversational facts, but is not a replacement for DeepLaw's
   protected-source identity, official catalog trust or explicit owner-authorized revisions.

Small licensed functions may later be copied or adapted when they solve a reproduced task failure.
Every such decision must record exact file/commit, rights and attribution, PRD outcome, adaptation,
tests and security boundary in the existing reuse notices. This review adds no runtime dependency
or vendored code, so it does not claim a completed supply-chain admission for these projects.

## Host complementarity and observability

Current primary interfaces include [Codex App Server](https://github.com/openai/codex/blob/main/codex-rs/app-server/README.md),
[Codex hooks](https://learn.chatgpt.com/docs/hooks),
[OpenAI API compaction](https://developers.openai.com/api/docs/guides/compaction),
[Claude Code memory](https://code.claude.com/docs/en/memory) and
[Claude Code hooks](https://code.claude.com/docs/en/hooks). These live links are observation-date
references, not frozen supported-version claims. Host-native persistence and compaction complement
DeepLaw; their native IDs, encrypted state and authentication are not a portable knowledge format.

A thin integration must distinguish configuration installed, hook invoked, request admitted,
checkpoint committed, correct revision restored and correct next action observed. A missing native
counter is unavailable, not zero; a no-model handshake is not a successful real-session recovery.
Automatic/guided/manual describes observable integration behavior, not merely a manifest promise.
DeepLaw owns bounded knowledge state, while Hosts own their lifecycle and actual tool execution.

## Delivery order and finite acceptance

The current delivery sequence is a planning decision, not new frozen gate results:

1. **Public-read closure:** initialize instructions agree with tools-list; a pure MCP client can
   progress from bounded context to exact admitted knowledge and Source evidence. Preserve the
   single `knowledge_support` read leaf through explicit contract versioning. Test wrong identity,
   stale/forgotten state, denial, pagination, hard output limits and no canonical writes. Existing
   v7 at the review baseline advertises only query/context/explain. The subsequent input v8 read
   candidate implements a bounded subset under [ADR 0007](adr/0007-exact-progressive-mcp-reads.md);
   its current scope belongs to the adapter contract, not this dated research sequence. Native
   Host and formal qualification remain separate obligations.
2. **Real Host closure:** assign engineering ownership to missing collector/broker producers and
   establish one genuine checkpoint/save/resume journey in a supported environment. Qualify the
   frozen required Host tasks afterward. Do not keep requesting paths to unspecified executables
   without identifying their source, deployment and observable fields.
3. **Candidate convergence:** integrate related fixes and documentation, run focused regressions,
   then the required stable-candidate checks once. Freeze a new exact candidate only after root
   causes are addressed. Old Candidate Full results remain evidence for their old commit.
4. **Kernel release:** retain existing Core/Capability/Competitive Claim distinctions and execute
   exact-artifact qualification. A Kernel release does not establish every Legal/Wiki/Host claim.
5. **Subsequent capability work:** qualify continuous Wiki maintenance, legal evidence duties and
   additional Hosts independently; investigate the retained first-query warmup before expanding
   scale. Do not bundle all six research targets into the current release.

For legal comparison, freeze the same corpus and versions, task set, model, tool access and total
budget against named BM25/dense/hybrid-rerank/graph baselines. Report wrong version, false Authority,
invalid locator, exception omissions, correct abstention and cost. Immutable sources alone do not
prove superiority over those methods.

For Wiki and continuity, freeze multi-step trajectories: source update, correction, human edit,
branch/fork, compaction/resume, stale write, interruption and selective forget. Measure First
Correct Action, Decision Preservation, Wrong-State Admission, evidence coverage, benign-state
preservation, recurrence of corrected errors and complete task cost. Give comparators equivalent
authorized input; separate component ablation from full-product comparison. Adversarial and benign
cases are both necessary, and unknowns must remain visible.

## Construction efficiency

- Keep one active integration owner and precise disjoint write scopes. Parallelize bounded code
  work, research and independent review only when they advance separate dependencies.
- Each work package carries one reproduced user failure, its public-seam success criteria, affected
  contracts, evidence invalidation and next action. The amount of control paperwork is not progress.
- Verify producers before adding consumers: identify how a real observation is generated before
  building another validator or asking for another hash/path. Never manufacture a missing metric.
- Reuse retained evidence for diagnosis with its original claim ceiling. Repeat a costly test only
  for a changed candidate, a new causal hypothesis, or a required exact-artifact gate.
- Run the full stable-candidate checks and Candidate Full at the appropriate integration boundary,
  rather than on every small diagnostic slice. Genuine failures still block their dependent gate.
- Keep the PR description focused on current behavior, validation and remaining release blockers;
  retain historical logs by reference. Keep machine status in qualification artifacts, contract
  semantics in canonical docs and research in dated reviews.

The leadership claim remains a Target until named, reproducible, cost-aware comparison supports it.
The nearer engineering opportunity is to make existing governance visible as useful, repeatable
product journeys and to correct propagated state without losing trustworthy accumulated work.
