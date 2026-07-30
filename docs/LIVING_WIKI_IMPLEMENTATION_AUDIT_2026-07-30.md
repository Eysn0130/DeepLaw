# Living Wiki Compiler implementation audit

Audit date: 2026-07-30
Classification: **target-architecture migration implemented in the current working tree**
Release status: **not yet a new release**

## Executive finding

Before this change, DeepLaw had the required evidence, Markdown/Ledger, grant, retrieval and
derived-index primitives, but the documented compounding Source-to-Knowledge lifecycle was still a
target. The working tree now contains one host-neutral Compilation Coordinator, explicit
dependency/freshness propagation, rich projection, purpose-aware retrieval, controlled backfill,
stable Python/CLI/MCP surfaces, bridge contracts and deterministic end-to-end evidence.

The local core loop is implemented and tested. This audit does **not** claim a new release gate:
the changed tree has not yet produced exact-commit three-OS evidence, an exact release artifact
manifest, real model-task receipts for every host, or any named comparative benchmark result.

## Pre-change capability and gap matrix

| Area | Reusable current capability | Gap found | Current working-tree result |
| --- | --- | --- | --- |
| Evidence | immutable bytes, Source Revision, Source IR, fragments, locators, parser risk | no semantic Run over one exact persisted IR | Run binds canonical source key, Source Revision, IR compilation and digest |
| Knowledge | typed Markdown revisions, stable IDs, CAS, Ledger, relations | no source-wide all-or-nothing semantic publication | staged Plans plus short atomic commit |
| Identity | semantic keys, normalized aliases, lookup, same-as/merge/split/ambiguity events | compiler could create avoidable aliases/duplicates | exact semantic reuse; alias collisions fail closed; ambiguity candidate |
| Governance | grants, operation allowlists, scope, sensitivity, lifecycle, audit | no compiler-specific capability surface | exact compiler operations; no ordinary mutation or self-grant |
| Dependencies | source references and current-source admission | no explicit revision-to-revision freshness graph | source/revision/Run dependencies and transitive propagation |
| Wiki | bounded navigation, communities and Canvas | 250-item compatibility truncation and limited typed pages | rich pages, >300 sharded indexes, local Canvas, full manifest |
| Retrieval | lexical/dense/graph/context and admission | no explicit purpose-to-policy contract | compiled-first/evidence-first/balanced v1 with visible fallback |
| Growth | ordinary sink/capture/consolidation | query reuse could become an implicit write | draft → validate → explicit promote backfill |
| Interfaces | CLI, read-only support MCP, opt-in sink MCP | no complete compiler loop or stable facade | shared Python API, CLI and MCP actions |
| Editors | open Markdown and generic reconcile | no bounded active-context bridge contract | Obsidian/Tolaria contracts, path policy and mock tests |
| Evaluation | release and external benchmark machinery | no frozen Living Wiki comparator/fixture inventory | preregistered protocol; all comparators `not_executed` |

## Implemented code and contracts

| Capability | Primary implementation | Contract/evidence |
| --- | --- | --- |
| Compilation saga | `src/deeplaw/compilation/` | `source-compilation-*.v1.schema.json` |
| Dependency/freshness | `compilation/freshness.py`, additive Ledger tables | `source-freshness-report.v1` |
| Rich projection | `src/deeplaw/projection/builder.py` | hash-bound Living Wiki manifest |
| Retrieval policy | `src/deeplaw/retrieval/purpose.py` | `purpose-aware-retrieval.v1` |
| Backfill | `src/deeplaw/backfill/service.py` | draft and receipt schemas |
| Stable facade | `src/deeplaw/api/knowledge_os.py` | public exception/result contracts |
| CLI | `knowledge compile`, `knowledge query`, `knowledge backfill` | same coordinator as API |
| MCP | support v4, sink v3 | frozen v1-v3 contracts retained |
| Editor bridge | `src/deeplaw/editor_bridge.py` | Editor Context, Obsidian and Tolaria schemas |
| Fake Agent | `benchmarks/hosts/deterministic_fake_agent.py` | executable E2E report schema |
| Real host harness | `run_living_wiki_host_harness.py` | explicit not-executed/pass/fail schema |
| Comparative preregistration | `benchmarks/living_wiki/` | protocol and fixture schemas |

## Invariants checked during implementation

- Source bytes and Source IR remain immutable evidence.
- Compiler output cannot request official/legal Authority, grant changes or arbitrary paths.
- Staging never enters normal recall.
- Objects and relations become visible only through one canonical transaction.
- Projection failure leaves canonical knowledge intact and recoverable.
- Query does not mutate the Ledger.
- Backfill cannot skip draft validation or explicit promotion.
- Protected Legal Pack evidence remains read-only and physically isolated.
- Bridges can write only declared noncanonical roots.
- Provider-visible packets and query results retain hard byte/item/token budgets.

## Evidence status

Implemented tests cover multi-packet atomicity, idempotency, restaging, empty-output rejection,
source binding, exact identity reuse, ambiguity preservation, synthesis and relation dependency
propagation, source successor/withdrawal, structural moved fragments, projection failure/retry,
durable recovery across every stable Run state, concurrent Plan compare-and-swap, safe abort,
>300 object sharding, purpose-aware read-only retrieval, backfill, API/CLI/MCP parity, migration,
snapshot/restore/rollback, bridge boundaries and deterministic fake-Agent E2E.

External evidence intentionally remains separate:

- real Codex, Claude Code, OpenCode and optional Gemini model tasks: **not executed for this tree**;
- Guanlan, traditional RAG, pure embedding, GraphRAG, exact Tolaria and exact Obsidian AI-plugin
  comparisons: **not executed**;
- Linux and Windows runs for this changed tree: **not executed**;
- exact release wheel/commit/schema/migration report: **not created**, because no release is being
  declared.

See the exact 48-item disposition in
[`LIVING_WIKI_ACCEPTANCE_REPORT_2026-07-30.md`](LIVING_WIKI_ACCEPTANCE_REPORT_2026-07-30.md).
