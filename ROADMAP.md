# DeepLaw roadmap

Status: v0.10.0 delivery line, updated 2026-07-30. DeepLaw 2.0 is the product brand, not the
software version. Runtime truth is `src/deeplaw`, contracts,
migrations, tests, and `uv.lock`. A roadmap item never becomes a delivery claim without those
artifacts.

## Permanent boundary

DeepLaw remains local-first, single-user, owner-controlled, CLI-first, and host-runtime agnostic.
The roadmap excludes multi-tenant SaaS, a remote canonical database, a team control plane, default
upload/telemetry, implicit networking, automatic legal adjudication, and authority derived from
rank, embeddings, graph weight, confidence, votes, or frequency.

Agent-derived knowledge may activate autonomously inside an explicit scope-bound policy. Official
identity, `human_verified`, signature/release operations, private export, scope expansion, new tool
permissions, and audit destruction remain owner/maintainer-only.

## v0.8 — Autonomous Knowledge Core

**Implemented in v0.9.0 and retained in v0.10.0.** The milestone is an additive migration from the published v0.7
foundation:

- additive migration with a verified v0.7 rollback point;
- immutable content-addressed objects plus canonical Markdown Knowledge Revisions;
- STRICT SQLite identity/event Ledger, bitemporal relations, writer/run/model/tool provenance, and
  independent hash-chain verification;
- stable-ID rename/move reconciliation, file leases, compare-and-swap, explicit conflict
  preservation, crash recovery, audited exact-duplicate collapse, high-precision contradiction
  preservation, prompt-injection and authority-elevation quarantine;
- separately enabled scope/sensitivity/operation/size/rate/capacity-bound `knowledge_sink`;
- Run Records, bounded durable capture, Claim/Concept/Entity/Event/Comparison/Synthesis/Memory/Skill
  revisions, alias and identity resolution, evidence-required canonical temporal relations, and
  source/run binding;
- immediate active/quarantine policy, immutable revision supersession lineage, TTL expiry,
  autonomous consolidation, explicit forgetting, owner-confirmed content GC, and fail-closed
  admission for every inactive lifecycle state;
- v3 read-only `knowledge_support`, v2 separately enabled `knowledge_sink`, bounded Capsule v2,
  foreground Watcher, recovery, replay, snapshot, restore, and v0.7 rollback;
- existing exact-byte signed Legal Pack updater with monotonic catalog sequence, rollback protection,
  fail-closed build validation, and atomic activation.
- autonomous snapshot/restore and legacy compatibility.

The v0.10 release line freezes the package/version, supported-OS matrix, security/package audit,
quality report, signed release metadata, and release notes independently from comparative evidence.

## v0.9 — Living Wiki and Knowledge Intelligence

**Implemented in v0.9.0 and retained in v0.10.0; cross-system comparison remains separate.**

- deterministic offline multilingual dense index and evidence-duty reranker, both bound to model
  identity, audit heads, revision inventory, exact index bytes, offline policy, and hard limits;
- exact, lexical, dense, graph, temporal, memory, Living Wiki, Source Tree/code-symbol compatibility,
  and hybrid planning under item/character/token/source/hop/provider-payload budgets;
- constrained aliases, same-as/merge/split identity decisions, Wikilink compilation, temporal
  canonical relations, weighted deterministic communities, contradictions, Semantic Lint, and Gap
  Discovery;
- Living Overview plus Concept/Entity/Event/Comparison/Synthesis pages, community/gap reports, and
  JSON Canvas as hash-bound rebuildable views;
- memory consolidation with crash-safe saga replay and reversible archived inputs;
- Obsidian/Tolaria-safe stable-ID rename/move/reconcile semantics without CRDT or last-writer-wins;
- a deterministic Skill Factory that compiles only explicitly checkable Procedure steps into a
  governed draft Skill revision; promotion still requires an admitted user/external evaluation;
- authority-partitioned `law_support.federated_context` for official, user-private, and explicitly
  enabled Agent interpretations, with no fallback relabeling or legal adjudication.
- canonical mutations enqueue derived maintenance instead of synchronously rebuilding the Vault;
  Watcher or explicit rebuild consumes the queue, while stale indexes fail closed and bounded
  canonical recall remains available.
- governance filters are applied before bounded lexical/dense/temporal/graph candidate windows and
  revalidated before reranking; Lint/gaps enforce the same scope/sensitivity boundary.

## DeepLaw 1.0 milestone — Quality and Superiority Closure

**Core quality engineering is implemented in software v0.10.0; comparative execution remains
open.** Evaluation Protocol v1 provides a public Benchmark, fixed scoring, a maintainer-visible
time-frozen holdout, actual autonomy/security and Typed Compiler suites, complete automatic
reports, independent verification, and an exact-wheel release gate. No external institution
certification is required. The 17-system comparison kit is retained for the following work:

- frozen corpus/candidate/split/questions and preregistered third-party baselines;
- fixed host model, prompt, permissions, context budgets, hardware, network, latency, memory,
  indexing and token/cost accounting;
- real-model Codex, Claude Code, and OpenCode tasks on the frozen candidate;
- actual RAGFlow, Graphiti, PageIndex, Mem0, OpenKB, LLM Wiki, Obsidian/Tolaria and registered
  baseline runs under one evaluator-owned environment;
- held-out task success, useful-context recall, irrelevant-context rate, provenance coverage,
  wrong-version/invalid-authority admission, temporal updates, contradiction, forgetting,
  poisoning, unauthorized mutation, isolation, abstention, cold/warm latency, and build cost;
- confidence intervals, paired tests, correction for multiple comparisons, complete failures,
  raw outputs, and full build/query cost records.

The exact release may set `quality_protocol_eligible=true`. Until the comparative artifacts exist,
`competitive_claim_eligible=false`; no better/leading/SOTA statement is permitted. Independent
replication is welcome but optional and does not create product Authority.

## Sequencing rule

At every milestone:

1. Preserve evidence bytes, stable identity, authority dimensions, scope, sensitivity, temporal
   state, provenance, and audit before adding retrieval sophistication.
2. Add one production-grade path with schema, migration, rollback, replay, tests, and docs; do not
   maintain a second implementation in GUI, MCP, Skill, or adapters.
3. Keep advanced retrieval and visual layers disposable and rebuildable.
4. Compare mechanisms fairly before making one a default.
5. Stop and report an evidence gap instead of filling it with a generated claim.
