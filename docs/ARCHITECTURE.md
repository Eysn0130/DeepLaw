# DeepLaw Architecture

Status: **v0.12.0 current architecture**, reviewed 2026-08-13. Historical v0.7 source-derived,
proposal/review, parser, and Legal Pack details remain in
[`KNOWLEDGE_OS.md`](KNOWLEDGE_OS.md), [`DOCUMENT_IR.md`](DOCUMENT_IR.md), and
[`DEEPLAW_2.md`](DEEPLAW_2.md). They are compatibility components, not the activation policy for
new Agent-derived knowledge.

## Continuity Pass 2 boundary (source candidate; not released)

Continuity Pass 2 is a narrow development correction after the retained **Pass 1** continuity
remediation. Pass 1's reviewed implementation boundary, historical Gold/protocol inputs, and local
verification evidence remain immutable evidence. The continuity correction is commit
`2f31bff4069e6cf01edf017134e5a760becb5360`; the semantic release-evidence correction is commit
`d7da1869287fd590d820f7dd60506abdcb826ad4`. This tracked note cannot bind its own final tree, and
no qualification wheel/report hash exists. The correction is kernel evidence only. It does not
lower any Core gate, qualify an end-to-end (E2E) workflow, or make a capability or competitive
claim.

The three reproduced defects and their minimum repairs are:

1. **Route candidates were still vulnerable to the ordinary candidate cut.** An exact route hit is
   an independent, bounded reservation before ordinary content selection. The no-route discovery
   ceiling remains `512`; an exact route reserves one slot and leaves at most `511` ordinary
   candidates, so the combined/global candidate and final Capsule budgets remain unchanged.
2. **Goal text could change task-route identity.** Retrieval uses `task + goal` as its query when a
   goal is supplied. The route digest is derived only from the canonical task text inside the
   domain coordinator; adapters and callers do not manufacture a route digest. A goal therefore
   enriches discovery without selecting a different checkpoint route.
3. **A route could expose multiple current checkpoint heads.** The first write for a route creates
   one Knowledge Object. A later write for that same object creates a new Knowledge Revision and
   must supply the current `expected_revision` compare-and-swap (CAS). A stale or concurrent write
   fails as `checkpoint_head_conflict`. A pre-fix multi-head projection is read as a sanitized
   Gap, never as a best-effort or last-writer-wins (LWW) result. The Owner reconciles it through the
   existing `forget`/withdraw lifecycle and projection rebuild; no historical revision is rewritten
   and no LWW choice is made.

The route projection is derived and rebuildable. The continuity correction adds no canonical
Knowledge table, migration, or sink schema, and the published `knowledge-sink.input/v2` bytes
remain unchanged. The resulting
compatibility boundary is semantic (new writes use the single-head/CAS policy; legacy bytes and
history remain readable and immutable), not a persistence-contract expansion.

The gate classification is explicit: **Core** gates remain required and are not lowered;
**Capability** gates may remain `not_claimed` when not declared (Run Timeline, semantic restore,
and Claude/OpenCode support remain deferred unless explicitly supported); the **Competitive Claim**
gate is independent and cannot be satisfied by local kernel or development evidence. Pass 2 records
`kernel=Implemented`, `E2E=Target`, and `external qualification=not_executed` for the affected
continuity/context rows only.

## Product boundary

DeepLaw is a local-first Agent Knowledge OS that preserves source materials in a source-native
evidence plane, compiles governed knowledge, projects a governed Living Wiki, and returns
verifiable, bounded knowledge context to Codex, Claude Code, OpenCode, and other Agent runtimes. It
does not own the model, conversation loop, general tool execution, legal adjudication, or a remote
control plane.

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

### Source-native Evidence Library and Wiki projection

The **Evidence Library** is the human/product role of the existing Evidence Core. It is not a new
store, policy plane, or retrieval engine. Laws, standards, policies, manuals, contracts, research
papers, and other professional sources remain exact Source Revisions in their original format with
Document/Version identity, source hierarchy, Fragments, Locators, parser provenance, and applicable
time. Derived text, OCR, layout, FTS, vectors, thumbnails, and previews are replaceable accelerators
bound to those exact bytes.

```text
source-native file / bytes
  -> immutable Source Revision + Document/Version/Locator
  -> rebuildable Document IR and search accelerators
  -> governed Knowledge Revisions and bounded Wiki navigation
  -> source-first drill-down when quotation, version, time, or completeness is required
```

The Living Wiki exposes readable identities, status, derived concepts, relationships, gaps, and
bounded evidence links. It does not become the canonical container for a professional document and
does not require full source transcription into editable Markdown. Protected source projections are
read-only evidence views; any human or Agent interpretation remains a separately governed Knowledge
Revision.

Current v0.12 process and storage isolation, especially for `law_support`, enforces trust,
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

### Current compilation status semantics

Compilation status is a reduction over immutable attempts and committed revisions, not a synonym
for the newest attempt. The two public status seams (`source_knowledge_status` and compilation
handoff) use one domain reducer and expose four independent facts:

- whether any canonical successful Knowledge Revision has been committed;
- whether that revision is admissible under the current Source lifecycle;
- the latest compilation attempt status, including a later failure or projection-pending attempt;
- whether the current Wiki projection is ready, pending, blocked or not started.

A later failed attempt never deletes or rewrites an earlier canonical success. It also is not
hidden behind a plain `compiled` label: the canonical revision can remain admissible while current
compilation state is `stale_or_blocked`. A successful attempt with incomplete projection remains
compiled and admissible while its Wiki status is pending. Withdrawal or another blocking Source
lifecycle preserves history but makes the committed revision inadmissible.

Golden synchronization, source add and reconciliation all enter through the existing auto-aware
Vault/domain coordinator and obtain the same status receipt. Golden sync does not define a second
mutation path or compilation state machine.

The v0.13 source candidate adds four bound layers without changing those authorities:

- Semantic Profile v3 computes dynamic applicability from the registered Source/IR, observations
  and owner profile. `unknown` or unresolved applicable duties block completeness.
- Statement Evidence core v1 persists stable statement ordinal/text/hash/type, exact evidence maps,
  independent receipts and dependency staleness in the semantic commit transaction.
- Statement/map/receipt logical artifacts retain one digest and Ledger reference per Statement, but
  new commits store their physical bytes in deterministic, bounded CAS bundles (at most 768 members
  and 8 MiB). The additive bundle-member table maps logical digest to bundle ordinal; old
  one-digest/one-file candidate Vaults remain readable. Verification hashes the bundle and every
  extracted logical member, snapshot/restore preserves the same CAS and Ledger identities, and
  owner content GC treats every compilation-artifact digest as referenced evidence rather than an
  orphan.
- Living Wiki projection Profile `standard` (the default) produces no per-object Canvas and pairs
  its file manifest with a v3 Page Registry, Link Index and Stable Resolver. All files are published
  through one crash-recoverable ownership transaction.
- The knowledge MCP lifespan owns one verified persistent read snapshot. A long-lived Python
  `KnowledgeOS` handle lazily uses the same runtime for `context.compile`; it does not create a
  second cache or duplicate Capsule assembly. Warm requests compare cheap
  database/audit/manifest identities before reuse; a changed identity invalidates the old
  snapshot before reopening and verification. Explicit `verify` always performs full verification,
  and Python callers can close the handle directly or with a context manager.

### Compiled-first retrieval policy

For ordinary reusable task context, the default is to prefer admitted compiled Knowledge
Revisions and typed relations over reprocessing raw fragments. This preference is conditional, not
a universal hard-coded ranking by object kind:

- the Query Plan and Knowledge Duties decide which concepts, entities, claims, procedures,
  syntheses, memory, contradictions, gaps, freshness state, or relations are relevant;
- exact citation, source verification, incomplete coverage, and authoritative or legal evidence
  duties may require evidence-first selection or direct source drill-down;
- raw fragments remain bounded verification and fallback material;
- Query Plan v6 selects admitted statement identities and projects independent Statement Evidence
  receipts. Each receipt binds the statement hash and frozen input set to exact provider-visible
  Source Revision, fragment, locator and quote hashes; incomplete cross-source coverage remains an
  explicit Gap. Discovery first uses the governed current/historical Knowledge indexes with the
  requested `retrieval_mode`, `graph_hops`, and integrity-selected canonical lexical fallback;
  Statement matching is then restricted to at most 20 discovered revisions and a 512-item
  candidate pool. It never selects a fixed global prefix of the Statement table. Discovery and
  Statement truncation are plan/receipt-bound, and a resource-bound truncation is a provider-visible
  Gap. Query Plan v5 retains its object-level Synthesis receipt as explicit compatibility;
- Provider Capsule v2 and its nested projection type Source references and Source evidence instead
  of admitting opaque objects. A Source evidence card must bind one exact Source Revision,
  fragment, locator and quote hash; if the complete passage does not fit the evidence budget, the
  passage is withheld and the applicable duty remains an explicit Gap rather than receiving a
  truncated passage labelled exact.
- working checkpoints use a separate bounded, indexed, rebuildable task-route projection before
  ordinary content discovery. Route identity binds opaque project, repository, stable-worktree,
  and task-line identifiers; checkpoint base/dirty state is a separate snapshot. An exact route
  candidate is an independent bounded reservation: it is admitted before ordinary content
  selection. With no route the ceiling remains `512`; reserving one route slot leaves at most
  `511` ordinary candidates, so the combined/global budget is unchanged. Exact-route admission
  cannot be displaced by the ordinary Top-20 and cannot widen
  its public selected count. Same-route snapshot divergence produces a sanitized Gap; route
  mismatch fails closed without an existence oracle. Every route row is revalidated against
  canonical Run/Revision/Ledger state; the projection is derived, rebuildable, and capped.
- bounded deterministic query-only aliases may improve cross-language discovery. They do not alter
  stored source or Knowledge text, indexes, identity, admission, or Authority. Query Plan v6 binds
  the expansion profile/count/digest and validates it as part of the query/audit receipt; v5
  continues to accept the additive v1/v2 expansion receipt shape. Natural-language capitalization
  and inferred identity anchors are bounded discovery/rerank/selection hints, not identity or
  Authority constraints; only caller-explicit stable identity targets remain strict;
- canonical graph views keep the 500-admitted / 5,000-scanned hard bounds and report selection
  truncation independently from candidate-scan truncation. A selection-truncated result requires
  an actually observed additional admitted Relation; a rejected-only tail cannot manufacture that
  signal. Both conditions carry bounded gaps, and Wiki local graph plus CLI/MCP reuse the same
  domain response;
- every fallback from compiled knowledge to source fragments must be observable in the plan,
  explanation, gap, or receipt;
- CLI, MCP, and Python use Query Plan v6 by default in the source candidate. The planner resolves
  applicable duties, selects statements, performs only duty-targeted evidence fallback, suppresses
  represented evidence and reports residual gaps. Query Plan v5 remains explicitly selectable. An exact
  policy/entity designator cannot be silently replaced by a different designator when the target
  revision is stale or withdrawn; the Capsule stays empty and preserves the explicit gap;
- The autonomous Context entry points share one domain assembler: Python
  `KnowledgeOS.context.compile`, `deeplaw knowledge context`,
  `deeplaw knowledge autonomy context`, and MCP `operation=context` default to Query Plan v6.
  The owner-local response is additive `deeplaw.knowledge-capsule/v3`, bounded to 262,144 bytes,
  and retains the complete v6 plan/hash, selected Statements, evidence, contradiction/gap state,
  receipt, budget, audit head, and `write_performed=false`. Its nested
  `deeplaw.provider-knowledge-capsule/v2` projection is independently bounded to 65,536 bytes;
  the provider receives only the bounded Statement/evidence projection and opaque `receipt_id`.
  The v3 local audit summary has no candidate scores, rejected-candidate text, query debug data,
  paths, or secrets. Explicit `query_plan_version=5` is compatibility-only: Python and CLI retain
  local Capsule v2, while MCP retains its output/v3 plus Capsule v2 compatibility envelope. The
  legacy `deeplaw recall` command remains the `retrieval_fabric` path and is not a Query Plan v6
  Context alias. Ordinary query and Context operations never append the canonical Knowledge Ledger;
  the bounded Query Trace is process-local and deletable by runtime-owner lifecycle actions;
- ordinary queries do not append to the canonical Knowledge Ledger. MCP keeps only a 16-entry,
  15-minute, 1 MiB aggregate ephemeral Query Trace after successful provider validation. The trace
  stores query hashes and redacted receipt metadata, verifies its hash on read, rotates by TTL/LRU,
  clears on identity change/close, and disappears on process exit. Candidate scores and audit
  projection internals do not cross the provider boundary; the provider-visible receipt contains
  only the opaque `receipt_id` join key;
- Living Wiki object pages retain inline Statement Evidence for small revisions. Revisions with
  more than 64 Statements project deterministic, registry-indexed Statement Evidence shards of at
  most 64 Statements each. The canonical object page links every shard, each Statement keeps its
  stable anchor and receipt metadata, and every generated page stays under the 256 KiB page bound;
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
and selection digest. Autonomous Context uses local Capsule v3 plus the nested Provider v2
projection described above; Provider content is capped at 65,536 bytes and contains no full plan,
candidate scores, rejected-candidate text, SQL, cache/parser diagnostics, paths, or secrets.
Restricted content never crosses MCP. The local Query Trace remains bounded, redacted and
non-persistent; it is not Canonical Knowledge or a Ledger mutation.

## Interface and process isolation

| Surface | Boundary |
| --- | --- |
| `knowledge_support` | One read-only stdio leaf; v3 exposes only its closed bounded read operations, including query, context, explain, identity, and gaps |
| `knowledge_sink` | Separate stdio leaf; additive input v5 for bound writes, frozen v2 legacy compatibility, explicit owner token, writer, scope, sensitivity, operation allowlist, byte/rate/capacity limits, idempotency, and audit |
| `law_support` | Separate read-only process and storage for signed official and owner-private legal evidence |
| CLI | Owner administration, source ingestion, grants, migration, rollback, snapshot, rebuild, official/private Legal operations |
| Watcher | Explicit foreground polling adapter over the same reconcile/coordinator service; no background daemon |
| Markdown/Obsidian/Tolaria | Open workspace; rename/move is identity-safe, content edits require reconciliation |

The default plugin registers only its read surface. No retrieval operation hides a write. Legal
build/update/upload/delete/signing never enters a query MCP. An OS process with arbitrary same-owner
shell access can bypass MCP and must be constrained by the host or a separate OS identity.

Official static and generated Host configurations enter these leaves through the fixed-target
closed launcher on the existing CLI commands. It creates an isolated HOME/USERPROFILE/XDG/temp
root, copies only portable process values plus explicit DeepLaw data/task settings, and can dispatch
only `knowledge_support`, `law_support`, or an owner-granted `knowledge_sink`. Host/provider auth,
plugin/hook state and credential paths do not enter the child. Host Connect Plan v2 keeps the local
Vault path outside generated configuration and checks the selected Vault's opaque identity before
starting the read process.

These isolated processes are deployment and trust boundaries within one governed Knowledge OS.
They must not evolve into disconnected identity, graph, versioning, or retrieval implementations.

Pass 14 Host qualification code shares one candidate/wheel/contract/report/bundle orchestrator.
Codex and OpenCode adapters keep only their Host protocol, closed configuration and sanitized event
interpretation. For the current Codex App Server protocol, `thread/compact/start` returns `{}` and
completion is the paired `contextCompaction` item lifecycle. Deprecated `thread/compacted` is a
compatibility input, never qualification evidence. This Host harness is development/qualification
infrastructure; it does not add a runtime, session store, retrieval engine or mutation authority to
DeepLaw.

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
For the current v0.13 source candidate, `release_gate_passed=false`,
`claim_eligible=false`, and `competitive_claim_eligible=false`; no release or superiority
conclusion follows from the local Context parity work.

## Pass 2 skip disposition

Pass 2 keeps these lanes explicit so a development skip cannot be mistaken for qualification:

| Lane | Disposition |
| --- | --- |
| Statement scale 10k | `required not_executed` |
| Statement scale 100k | `required not_executed` |
| Relation truncation 500/5000 | `required not_executed` |
| Wiki wrong merge | `required not_executed` |
| Wiki alias collision | `required not_executed` |
| Wiki cycle | `required not_executed` |
| Historical v0.6 wheel | `separate compatibility not_executed` |
| Windows native ACL | `macOS not_applicable`; Windows evidence remains required |
| Windows native junction | `macOS not_applicable`; Windows evidence remains required |
