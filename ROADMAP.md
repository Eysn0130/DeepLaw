# DeepLaw roadmap

Status: repository-head vNext, updated 2026-07-29. Runtime truth is `src/deeplaw`, contracts,
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

## v0.8 — autonomous Markdown core

Repository-head implementation:

- additive migration with a verified v0.7 rollback point;
- immutable content-addressed objects plus canonical Markdown Knowledge Revisions;
- STRICT SQLite identity/event Ledger, bitemporal relations, writer/run/model/tool provenance, and
  independent hash-chain verification;
- stable-ID rename/move reconciliation, explicit conflict preservation, crash recovery, exact
  duplicate rejection, prompt-injection and authority-elevation quarantine;
- separately enabled scope/sensitivity/operation/size/rate/capacity-bound `knowledge_sink`;
- immediate active/quarantine policy, immutable revision supersession lineage, TTL expiry, explicit
  forgetting, and fail-closed admission for every inactive lifecycle state;
- v2 read-only `knowledge_support` with source-derived/autonomous partitions, lineage, graph,
  Living Wiki discovery, feedback records, and bounded Capsule v2;
- deterministic FTS, semantic lint, connected components, Wiki/Canvas rebuild manifests;
- autonomous snapshot/restore and legacy compatibility.

Release closure still requires a clean frozen artifact, package/version decision, supported-OS
matrix, security/package audit, signed release metadata, and published release notes. Repository
implementation is not itself a released wheel claim.

## v0.9 — hybrid retrieval and knowledge quality

Planned only until implemented and evaluated:

- bind optional local embedding and reranker indexes to exact model/checkpoint/configuration,
  input revision set, audit head, index bytes, network policy, and resource budget;
- add model-assisted concept/entity/event extraction with closed outputs, source/Knowledge Revision
  back-links, poisoning checks, and deterministic fallback/abstention;
- compare Source Tree navigation, lexical retrieval, semantic retrieval, graph traversal,
  GraphRAG communities, Wiki navigation, memory, and hybrids under one frozen protocol;
- implement contradiction clusters, evidence-duty coverage, semantic consolidation proposals,
  usage/feedback-based decay candidates, and owner policy for content-erasing forget/GC;
- evaluate Skill trigger precision/recall, capability boundaries, failure paths, context cost,
  revision rollback, and cross-host thin adapters.

## v1.0 — reproducible real-task proof

Planned release gate:

- frozen corpus/candidate/split/questions and preregistered third-party baselines;
- fixed host model, prompt, permissions, context budgets, hardware, network, latency, memory,
  indexing and token/cost accounting;
- held-out task success, useful-context recall, irrelevant-context rate, provenance coverage,
  wrong-version/invalid-authority admission, temporal updates, contradiction, forgetting,
  poisoning, unauthorized mutation, isolation, abstention, cold/warm latency, and build cost;
- confidence intervals, paired tests, correction for multiple comparisons, complete failures, raw
  outputs, independent organizations, and signed reproducibility artifacts;
- threat-model closure, formal migrations, upgrade/rollback drills, Windows/macOS/Linux evidence,
  SBOM/licenses/OpenVEX, and release signing.

Until those artifacts exist, `competitive_claim_eligible=false`; no better/leading/SOTA statement
is permitted.

## Sequencing rule

At every milestone:

1. Preserve evidence bytes, stable identity, authority dimensions, scope, sensitivity, temporal
   state, provenance, and audit before adding retrieval sophistication.
2. Add one production-grade path with schema, migration, rollback, replay, tests, and docs; do not
   maintain a second implementation in GUI, MCP, Skill, or adapters.
3. Keep advanced retrieval and visual layers disposable and rebuildable.
4. Compare mechanisms fairly before making one a default.
5. Stop and report an evidence gap instead of filling it with a generated claim.
