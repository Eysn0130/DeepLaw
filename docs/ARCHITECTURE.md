# DeepLaw Architecture

Status: **v0.11.0 current architecture**, reviewed 2026-07-30. Historical v0.7 source-derived,
proposal/review, parser, and Legal Pack details remain in
[`KNOWLEDGE_OS.md`](KNOWLEDGE_OS.md), [`DOCUMENT_IR.md`](DOCUMENT_IR.md), and
[`DEEPLAW_2.md`](DEEPLAW_2.md). They are compatibility components, not the activation policy for
new Agent-derived knowledge.

## Product boundary

DeepLaw is a local-first Agent Knowledge OS that compiles source materials into a governed Living
Wiki and returns verifiable, bounded knowledge context to Codex, Claude Code, OpenCode, and other
Agent runtimes. It does not own the model, conversation loop, general tool execution, legal
adjudication, or a remote control plane.

DeepLaw is logically one governed knowledge system with multiple policy planes, not a collection of
disconnected knowledge products or databases. A plane defines origin, Authority, scope,
sensitivity, lifecycle, and write policy; it does not justify a separate identity model, graph
semantics, version system, or retrieval engine.

The current durable model has two primary semantic domains:

1. **Immutable evidence:** signed official Legal Pack bytes, user-provided originals, explicit Web,
   Git, tool, Run, and other Source snapshots, structured Source IR, stable fragments, locators, and
   parser provenance.
2. **Compounding governed knowledge:** source-derived and Agent-derived claims, concepts, entities,
   events, decisions, procedures, experiences, preferences, comparisons, syntheses, memories,
   relations, and versioned Skills.

Protected authoritative packs are Authority classes within the evidence domain. Agent
interpretations derived from them remain governed knowledge with `legal_authority=false`; even a
human-verified interpretation does not become the authoritative source itself. SQLite and derived
indexes support these domains. They do not create another source of Authority.

Current v0.11 process and storage isolation, especially for `law_support`, enforces trust,
capability, and privacy boundaries. It does not make the Legal Pack a second Knowledge OS or permit
duplicated domain logic. Logical unity does not require one physical database or weakening
official/private isolation.

## Six stable components

| Component | Responsibility | Canonical state |
| --- | --- | --- |
| Evidence Core | Preserve exact source and committed revision bytes with content hashes, fragments, locators, and lifecycle | Content-addressed objects plus source/revision records |
| Autonomous Knowledge Workspace | Expose Markdown/YAML/Wikilink knowledge for Agent and human editing | Registered immutable Markdown Revision Object; workspace file is its materialized current copy |
| Knowledge Ledger | Decide identity, current pointer, source binding, relation, Authority, scope, sensitivity, bitemporal state, lineage, writer, recovery, and audit | `.deeplaw/ledger.sqlite3` STRICT tables plus append-only hash-chain events |
| Cognitive Index Layer | Accelerate lexical, dense, tree, graph, temporal, community, Wiki, and reranker discovery | Disposable, hash-bound derived files and tables |
| Knowledge Runtime | Plan, discover, admit, challenge, select, and compile task context | Hashable Query Plan and bounded Knowledge Capsule |
| Agent and Human Interfaces | Provide CLI, read-only query MCP, explicit mutation MCP, Markdown, Obsidian/Tolaria, Canvas, and Skills | Thin adapters calling the same domain services |

## Storage and truth domains

```text
immutable object repository
  + Markdown-native knowledge space
  + SQLite trusted identity/event ledger
  + rebuildable retrieval and visualization indexes
```

There is no undifferentiated “single file is truth” rule. Each information domain has one
normative authority:

| Domain | Normative authority |
| --- | --- |
| Official/user source content | Exact content-addressed bytes and immutable Source Revision |
| Agent knowledge body | Exact bytes of the registered Markdown Revision Object |
| Current editable copy | Recoverable Markdown workspace materialization |
| Stable ID, current revision, Authority, lifecycle, scope, sensitivity | SQLite Ledger |
| Canonical typed relation and bitemporal interval | SQLite relation revision |
| FTS, vectors, Source Tree acceleration, adjacency, communities, Wiki navigation, Canvas | Rebuildable derived state |
| Current task delivery | Verified Knowledge Capsule bound to plans and audit heads |

A complete knowledge version is:

```text
Stable Knowledge ID
  + exact Markdown bytes and SHA-256
  + immutable object identity
  + Ledger Revision record
  + source/run/generation references
```

The Vault layout is:

```text
vault/
├── sources/                  # readable evidence views; exact bytes are also in CAS
├── knowledge/                # claim/concept/entity/event/decision/procedure/...
├── memory/                   # working/episodic/semantic/procedural/reflective
├── wiki/                     # generated navigation plus typed knowledge links
├── skills/                   # versioned Skill Knowledge Objects
├── attachments/
├── canvas/                   # disposable JSON Canvas views
├── AGENTS.md
└── .deeplaw/
    ├── objects/sha256/       # source and Knowledge Revision exact bytes
    ├── ledger.sqlite3
    ├── capabilities/        # owner-only sink tokens
    ├── staging/             # commit recovery and preserved conflicts
    ├── derived/             # indexes, communities, Wiki manifests, caches
    ├── snapshots/
    └── manifest.json
```

## Commit architecture

All persistent knowledge paths share `AutonomousKnowledgeStore` and the same coordinator. CLI,
MCP, and Watcher do not implement independent mutation rules.

```text
request or external Markdown edit
→ stage and parse stable ID/YAML
→ validate capability, schema, Authority, scope, sensitivity, provenance, case boundary, and risk
→ compare idempotency/base revision and preserve semantic conflicts
→ publish exact Markdown bytes to CAS (idempotent)
→ BEGIN IMMEDIATE
→ append immutable Revision, relation/lifecycle state, event, usage, recovery intent, rebuild work
→ commit Ledger transaction
→ atomically materialize current Markdown copy
→ asynchronously rebuild disposable indexes and views
```

The canonical path uses a Ledger-backed single-writer lease, base revision, compare-and-swap, and
explicit conflict revisions. It intentionally does not use character-level CRDT merge. If the
Ledger transaction fails, the current pointer and workspace do not switch; an unreferenced CAS
object is later eligible for GC. If materialization fails after commit, startup recovery replays the
pending intent. If derived maintenance fails, the knowledge revision stays valid and the rebuild
request stays pending.

Every persistent mutation appends a new revision/event. There is no silent in-place rewrite.
Current state tables answer current queries efficiently; the append-only hash chain supplies audit,
replay, and tamper evidence. DeepLaw is not a fully event-sourced database.

## Authority, provenance, and lifecycle

These dimensions remain independent:

- origin: official, user source, source-derived, Agent-derived, external import;
- verification: signature, user provision, deterministic/source binding, Run binding, unverified;
- lifecycle: active, superseded, revoked, expired, forgotten, quarantined;
- epistemic state: supported, tentative, contested;
- scope and sensitivity;
- writer, model/tool/activity, source references, and revision lineage;
- valid time and transaction/recorded time.

Ranking, embeddings, link count, community weight, feedback frequency, or model confidence cannot
change any Authority, source identity, legal effect, or capability. A normal sink write is fixed to
`agent_derived`, `legal_authority=false`, `revision_only`, and the grant's exact scope. A Skill is
knowledge, not an executable permission grant.

Every new canonical relation binds stable endpoints, a typed predicate, at least one admitted
evidence reference, writer, revision, scope, sensitivity, valid interval, and transaction time.
Historical source-free compatibility rows remain auditable but are not admitted to current graph or
recall. Wikilinks and Canvas are editing/navigation forms, not relation Authority.

## Autonomous growth

The active lifecycle is:

```text
Capture → Classify → Bind → Reconcile → Commit → Connect → Retrieve/Learn → Decay/Forget
```

Admitted ordinary Agent-derived knowledge becomes immediately active; it does not enter the v0.7
universal proposal queue. Quarantine remains for malformed schema, unknown or damaged provenance,
path escape, stored prompt injection, Authority elevation, capability breach, and integrity
failure. Official/human-verified promotion, signing, release publication, scope expansion,
restricted export, audit destruction, and new capabilities remain owner/maintainer operations.

Working memory requires an expiry. Feedback records evaluator identity; Agent self-report has less
governance weight than an owner-granted user or external evaluator. Consolidation creates a new
summary revision and evidence-bound relations before archiving inputs. Forgetting removes current
eligibility through a lifecycle revision; owner-confirmed GC is a separate byte-erasure policy that
never deletes evidence objects or governance history.

## Working-tree extension: compounding Source-to-Knowledge compilation

Status: **Implemented in the current working tree; not yet part of a new release.** The contracts,
additive persistence, coordinator and tests are documented in
[`LIVING_WIKI_COMPILER.md`](LIVING_WIKI_COMPILER.md). Exact release, three-OS and external evidence
remain pending in the acceptance report.

DeepLaw ingests a source revision once, compiles it into durable typed knowledge, and
incrementally maintains that knowledge as sources and tasks evolve. It must not regress into a
traditional RAG loop that repeatedly treats raw fragments as the primary reusable knowledge object
for every query.

The implemented compilation lifecycle is:

```text
source ingestion
→ immutable Source Revision
→ extraction and Source IR
→ semantic compilation plan
→ identity and evidence validation
→ governed Knowledge Revisions and typed relations
→ rebuildable Living Wiki and indexes
→ compiled-first retrieval
→ evidence drill-down when required
```

Compounding updates:

- resolve or preserve stable semantic identities instead of creating avoidable duplicates;
- create new revisions rather than rewrite prior knowledge;
- refresh affected objects and relations when sources change;
- preserve evidence bindings and record unresolved identity, contradiction, freshness, coverage,
  and compilation failure states;
- keep editor drafts and derived Wiki, graph, Canvas, cache, and index files out of normal
  retrieval until a governed Knowledge Revision is committed.

Models may propose extraction, synthesis, relation, and refresh plans. Deterministic DeepLaw code
must remain authoritative for schema validation, identity resolution, source and evidence binding,
grant and operation checks, scope, sensitivity, Authority, conflict handling, idempotency, atomic
commit, audit, and recovery.

### Compiled-first retrieval policy

For ordinary reusable task context, the default is to prefer admitted compiled Knowledge
Revisions and typed relations over reprocessing raw fragments. This preference is conditional, not
a universal hard-coded ranking by object kind:

- the Query Plan and Knowledge Duties decide which concepts, entities, claims, procedures,
  syntheses, memory, contradictions, gaps, freshness state, or relations are relevant;
- exact citation, source verification, incomplete coverage, and authoritative or legal evidence
  duties may require evidence-first selection or direct source drill-down;
- raw fragments remain bounded verification and fallback material;
- every fallback from compiled knowledge to source fragments must be observable in the plan,
  explanation, gap, or receipt;
- no rank, confidence, link count, community weight, or feedback signal may upgrade Authority.

The closed loop is:

```text
ingest once
→ compile durable knowledge
→ reconcile and refresh
→ reuse across Agents and tasks
→ verify against original evidence
→ rebuild derived views
→ recover safely from failure
```

## Cognitive and Wiki layers

The rebuildable layer includes current FTS/BM25, deterministic offline multilingual hash-dense,
evidence-duty reranker, Source Tree/query acceleration, graph adjacency, deterministic communities,
Living Wiki navigation, Semantic Lint, gap reports, JSON Canvas, and caches. Every manifest binds
the input audit heads, generator/model identity, configuration, revision inventory, exact bytes,
and hashes.

Canonical writes enqueue derived maintenance. An explicitly running Watcher drains it after each
reconcile cycle; operators may run `deeplaw knowledge autonomy rebuild` directly. Retrieval rejects
stale/damaged dense or lexical manifests. Current lexical reads use a scope-filtered bounded
canonical fallback and report truncation instead of presenting stale output as complete.

Typed Concept, Entity, Event, Comparison, and Synthesis objects are canonical Agent knowledge.
Overview, backlink, timeline, community, gap, report, and Canvas files are derived. Editing a
derived view cannot mutate source evidence; useful new prose must enter through a governed
Knowledge Revision.

## Retrieval and context

```text
Discovery != Admission != Selection != Authority != Adjudication
```

The runtime processes:

```text
Task → intent/duties → channel plan → candidate discovery/fusion
→ Authority/lifecycle admission → contradiction/counterevidence challenge
→ token/source/item/hop/payload optimization → Knowledge Capsule
```

Scope, sensitivity, lifecycle, valid time, kind, and required tags are pushed before bounded
lexical/dense/graph candidate cuts where the canonical channel supports them, then revalidated in a
single admission pass before reranking. This prevents unauthorized or irrelevant high-ranked items
from occupying the candidate window. Resource limits and incomplete scans produce explicit gaps.

The Capsule partitions official evidence, user-private evidence, source-derived knowledge,
Agent-derived knowledge, Agent memory, contradictions, limitations, gaps, and receipts. The general
Knowledge server leaves legal-evidence partitions empty; `law_support` owns them. Every plan binds
filters, budgets, selected revisions, both audit heads, derived manifests, candidate-state digest,
and selection digest. Provider-visible payloads have hard bounds, and restricted content never
crosses MCP.

## Interface and process isolation

| Surface | Boundary |
| --- | --- |
| `knowledge_support` | One read-only stdio leaf; v3 exposes twelve bounded read operations, including explain, identity, and gaps |
| `knowledge_sink` | Separate stdio leaf; explicit owner token, writer, scope, sensitivity, operation allowlist, byte/rate/capacity limits, idempotency, and audit |
| `law_support` | Separate read-only process and storage for signed official and owner-private legal evidence |
| CLI | Owner administration, source ingestion, grants, migration, rollback, snapshot, rebuild, official/private Legal operations |
| Watcher | Explicit foreground polling adapter over the same reconcile/coordinator service; no background daemon |
| Markdown/Obsidian/Tolaria | Open workspace; rename/move is identity-safe, content edits require reconciliation |

The default plugin registers only its read surface. No retrieval operation hides a write. Legal
build/update/upload/delete/signing never enters a query MCP. An OS process with arbitrary same-owner
shell access can bypass MCP and must be constrained by the host or a separate OS identity.

These isolated processes are deployment and trust boundaries within one governed Knowledge OS.
They must not evolve into disconnected identity, graph, versioning, or retrieval implementations.

## Legal Pack isolation

The official layer verifies exact catalog bytes with Ed25519 before parsing or downloading,
enforces public trust roots, key revocation, catalog identity, monotonic sequence, rollback
protection, immutable releases, historical pinning, and atomic active-pointer switching. Network
catalogs never use unsigned development bypass.

The user-private library is owner-only, content-addressed, unverified reference material. It cannot
inherit official identity by filename or appearance, and private add/delete cannot alter official
catalog, pointer, cache, ranking, receipt, or release. Federated legal context admits official,
private, and explicitly tagged Agent interpretation partitions independently. Agent interpretation
always remains `legal_authority=false`. DeepLaw supplies evidence and context, not legal
applicability or a verdict.

## Recovery, backup, and compatibility

Snapshots use a consistent SQLite backup plus canonical Markdown, CAS, staging/conflicts, evidence
views, Inbox provenance, manifest, and capability state. Derived layers are excluded and rebuilt.
Capability state contains owner-only token material, so snapshots are credentials. Migration from
v0.7 first creates and verifies a rollback point, atomically promotes the Ledger path, installs the
autonomous tables/workspaces, and binds legacy evidence. Rollback retains the replaced Vault in a
sibling recovery directory.

The v0.7 source-derived compiler, human review, Proposal Inbox, Workbench, Source IR/Tree, and
Retrieval Fabric remain valid for deterministic source compilation and untrusted imports. They are
queried as a distinct compatibility partition and cannot reactivate the universal-review policy for
ordinary Agent knowledge.

## Non-goals

- pure Markdown or pure SQLite as a universal truth store;
- vector or generated graph state as Authority;
- universal human review for all Agent knowledge;
- arbitrary Agent mutation of sources or Legal Pack;
- CRDT, Git, Neo4j, Elasticsearch, PostgreSQL, or a remote service as the core;
- fully event-sourced current reads;
- duplicated CLI/MCP/Watcher business logic;
- a GUI that becomes the sole control plane or duplicates CLI/MCP domain logic;
- independent knowledge engines or databases for individual policy planes;
- automatic legal adjudication;
- superiority claims without frozen real-task and named-comparator evidence.

## Verification and claim boundary

Every contract change requires schema, migration/rollback, replay/integrity, tests, and user docs.
Repository delivery runs:

```bash
uv run pytest
uv run ruff check .
git diff --check
```

Core quality is governed by the public, time-frozen, exact-wheel
[`Evaluation Protocol`](EVALUATION_PROTOCOL.md); external institution certification is not
required. Engineering completion still does not prove competitive superiority. Real three-host
tasks, actual named third-party results, paired confidence intervals, and retained
failures/resources remain governed by
[`EXTERNAL_BENCHMARK_PROTOCOL.md`](EXTERNAL_BENCHMARK_PROTOCOL.md). Until those comparative facts
exist, `competitive_claim_eligible=false` is mandatory.
