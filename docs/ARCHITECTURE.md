# DeepLaw Architecture

Status: **normative current architecture constitution**
Reviewed: **2026-08-17**

This document is the sole current architecture specification for DeepLaw. It defines the stable
product boundary, governed kernel, runtime data flow, and deployment/security boundaries. Runtime
facts remain authoritative in `src/deeplaw`, tests, JSON Schemas, migrations, `pyproject.toml`, and
`uv.lock`; subsystem documents linked from this file provide detailed contracts without creating a
second architecture source of truth. Historical proposals and qualification evidence are linked as
history only and are not copied into this constitution.

## 1. Frozen architecture

DeepLaw is a local-first, single-user, owner-controlled Agent Knowledge OS. It preserves exact
evidence, governs durable knowledge, projects a Living Wiki, and compiles bounded context for thin
Host drivers. It does not own a model, conversation loop, Agent runtime, general tool executor,
legal adjudication, remote canonical database, or cloud control plane.

The product has exactly three roles on one shared governance kernel:

| Product role | Owns | Does not become |
| --- | --- | --- |
| **Task Continuity / Governed Project Knowledge** | Task-lineage-specific goals, decisions, constraints, verified facts, gaps, next actions, and bounded Artifact references | A transcript archive, Host memory store, or second task database |
| **Source-native Evidence Library** | Exact source bytes, Document/Version identity, Fragments, Locators, parse provenance, temporal state, and source-first drill-down | A full editable Wiki copy or a generic chunk store |
| **Living Wiki** | Human/Agent-readable projections of governed identities, relations, evidence links, freshness, limitations, and gaps | Canonical professional source content, a new Authority system, or a second knowledge engine |

Legal Pack is the first-party legal policy plane of the Source-native Evidence Library. It remains
physically and governably isolated where trust requires it, while reusing the shared identity,
provenance, revision, retrieval, and audit semantics. It is not a fourth product or knowledge
engine.

All three roles use one Context Compiler:

```text
Discovery -> Admission -> Selection
  -> bounded, verifiable Knowledge Capsule
  -> thin Codex/OpenCode/other Host drivers
```

The Context Compiler is a shared kernel service, not a fourth product and not a second retrieval
engine. Product roles may not introduce another identity, lifecycle, governance, persistence, or
retrieval implementation.

Automatic transcript memory is prohibited. DeepLaw MUST NOT scrape, import, summarize, or persist a
Host transcript, hidden reasoning, raw logs, authentication, or complete diff in the background.
Continuity state enters the governed system only through the existing bounded, attributable,
owner-governed checkpoint and mutation paths.

The architecture is deliberately closed against speculative expansion. A new database, Knowledge
kind, Relation predicate, page family, Host adapter, Agent runtime, Connector, telemetry path, GUI,
or cloud control plane requires a reproduced external task failure, a mapped PRD outcome, an ADR,
migration/recovery and security evidence, and explicit Owner approval. Without all of those, the
existing primitives are the boundary.

## 2. Shared governed kernel

The kernel provides one set of stable semantics to all three roles:

- immutable Source Revisions with exact bytes, CAS identity, source Registry, Document/Version,
  Fragment, Locator, parser identity, and parse-risk provenance;
- governed Knowledge identities and immutable Knowledge Revisions with source/run lineage,
  content hashes, typed Relation Revisions, and recoverable current pointers;
- a Ledger for Authority, origin, verification, scope, sensitivity, lifecycle, valid time,
  transaction time, writer, grants, idempotency, contradiction, Gap, and audit receipt state;
- one domain Coordinator for durable mutations, used by CLI, MCP, reconciliation, compilation,
  and future clients; and
- backup, forget, recovery, replay, integrity verification, and rebuildable derived state.

These dimensions stay independent. A hash, signature, citation, embedding, reranker score, link
count, model confidence, feedback signal, or usage frequency cannot create Authority, widen scope,
grant permission, establish legal effect, or prove task applicability.

### Truth domains

| Information | Normative authority |
| --- | --- |
| Official or user source material | Exact content-addressed bytes and immutable Source Revision |
| Agent/user-authored knowledge body | Exact bytes of the registered Markdown Revision Object |
| Stable identity, current revision, grants, Authority, lifecycle, scope, sensitivity, time, lineage, and audit | Trusted Ledger |
| Typed relation and bitemporal interval | Governed Relation Revision and Ledger record |
| FTS, vectors, graph adjacency, communities, Wiki navigation, Canvas, rankings, and caches | Rebuildable derived state bound to its inputs |
| Current task delivery | Knowledge Capsule and receipt bound to the selected revisions and audit heads |

Markdown paths, filenames, titles, aliases, frontmatter, Wikilinks, Canvas nodes, and provider
sessions are open or untrusted surfaces. None can independently grant identity, Authority,
capability, scope, or permission.

### Mutation and recovery invariant

All durable writes follow one recoverable path:

```text
request or reconciled edit
  -> parse and stage
  -> validate identity, schema, provenance, grant, scope, sensitivity, lifecycle and risk
  -> compare base revision and idempotency key
  -> publish exact bytes to CAS
  -> append revision, relation/lifecycle state, event and recovery intent
  -> commit Ledger transaction
  -> materialize the current Markdown copy
  -> rebuild disposable indexes and projections
```

A failed transaction leaves the current pointer unchanged. A post-commit materialization failure
is recovered from the pending intent. A derived-index failure leaves canonical knowledge valid and
keeps rebuild work pending. No read operation, Query, Context, Wiki projection, or `law_support`
read may hide a durable write.

## 3. Product-role view

The roles are different user-facing purposes of the same governed state trajectory:

### Task Continuity / Governed Project Knowledge

Task state is bound to explicit Vault, project, repository, worktree, and task-lineage identity.
Run and Checkpoint records contain only bounded goal, decision, constraint, verified-fact, gap,
next-action, and Artifact references. New, resumed, forked, concurrent, and compacted threads
resolve the applicable lineage or return a structured Gap. An opaque Host session hint is never
the Knowledge identity, and task handles are optional lookup aids rather than static product
configuration.

Host-native memory and compaction remain complementary external facilities. A host may propose a
checkpoint, but only the governed, explicit mutation path can make it durable. Ordinary continuity
reads are read-only and do not append the Ledger. Selective forget updates lifecycle eligibility
through an attributable owner operation; it is not pointer rewind or silent deletion of history.

### Source-native Evidence Library

An acquired byte sequence becomes an immutable Source Revision. Changed bytes create a successor;
the prior revision remains addressable subject to lifecycle and authorized forgetting rules. Source
order, structure, stable Fragments, Locators, hashes, parser identity, applicable time, and parse
risk survive compilation. OCR, layout trees, extracted text, previews, FTS, and vectors are
replaceable accelerators bound to the exact Source Revision and their generator/configuration hash.

Professional or protected source material remains source-native. The Wiki may expose a bounded
evidence card, catalog entry, derived concept, status view, or link, but it must resolve exact
Document/Version/Fragment/Locator evidence on demand when quotation, effective date, exception,
proviso, cross-reference, or completeness duties require it. A Wiki excerpt never replaces the
source or changes Source Authority.

### Living Wiki

The Wiki is a governed, readable projection of stable identities, revisions, typed relations,
evidence links, freshness, limitations, conflicts, and Gaps. Rename/move preserves identity;
content edits become attributable revision proposals through reconciliation. Full and incremental
builds over the same canonical inputs must be semantically equivalent and must preserve user-owned
files. Protected source projections, DeepLaw-owned derived pages, governed editable knowledge, and
user-owned files have explicit edit, rebuild, and deletion behavior.

The Page Registry, Link Index, Resolver, and any Canvas or navigation files are derived state. They
must remain bounded, rebuildable, and equivalent in semantic identity and visible Gap behavior.
Physical artifact layout may use bundling or sharding when scale requires it; one file per logical
record is not an architectural invariant.

## 4. Runtime data-flow view

The runtime keeps discovery, admission, selection, Authority, and adjudication separate:

```text
Discovery != Admission != Selection != Authority != Adjudication
```

### Source-to-Knowledge flow

The Cognitive Index Layer, including Source Tree, code-symbol search, graph adjacency, communities,
and query accelerators, is disposable derived state. None of it grants identity or Authority.

```text
Source Revision
  -> Source IR, stable Fragments, Locators, and parse provenance
  -> typed Knowledge Objects and Relation Revisions
  -> governed Knowledge Revisions and Ledger events
  -> rebuildable indexes and Living Wiki projections
```

Models may propose extraction, synthesis, identity, relation, or refresh plans. Deterministic
DeepLaw code controls schema validation, identity resolution, source binding, grants, scope,
sensitivity, Authority, conflict handling, idempotency, commit, audit, and recovery. Repeated
unchanged ingestion is idempotent; changed inputs invalidate only actual dependents. Unsupported,
contradictory, stale, unverifiable, or incomplete compilation remains an explicit Gap.

### Context Compiler flow

```text
task intent and duties
  -> bounded discovery over admitted indexes and source/revision identities
  -> scope, sensitivity, lifecycle, time, Authority and task-purpose admission
  -> contradiction, exception, freshness and temporal challenge
  -> duty-targeted evidence fallback and deduplication
  -> bounded item/source/character/token/hop/payload selection
  -> local Knowledge Capsule and receipt
  -> Provider-safe projection to a thin Host driver
```

Discovery proposes candidates only. Selection cannot upgrade Authority or bypass a Gap. A fallback
from compiled knowledge to raw Fragments remains bounded and visible in the plan, explanation,
receipt, or Gap. Missing or unverifiable evidence is not replaced with model memory, unrelated Web
content, or a plausible source.

The current public provider advertisement is `knowledge-support.input/v7` /
`knowledge-support.output/v6` (`knowledge-support input v7/output v6`) and exposes only `query`,
`context`, and `explain`. Earlier input v1-v6 and output v1-v5 shapes remain
compatibility/internal where implemented; they are not current public capability claims.
Provider-visible bytes contain
only admitted, bounded task context, minimum evidence and
limitations, structured Gaps, and an opaque receipt join key. They never contain paths, raw logs,
transcripts, reasoning, Secret material, rejected-candidate text, complete scores, SQL, cache
diagnostics, or unadmitted content. Ordinary reads leave the canonical Ledger unchanged.

### Rebuild and projection flow

```text
canonical revision/audit heads
  -> input-bound generator and configuration
  -> derived index / Page Registry / Link Index / Wiki / Canvas
  -> content hash and manifest
```

Every derived artifact records its input revisions or audit heads, generator/model version,
configuration, and content hash. Damaged or stale derived state is rejected or rebuilt; it cannot
silently become a new source of truth.

## 5. Deployment/security view

DeepLaw uses thin process and adapter boundaries around the shared kernel:

| Surface | Boundary and authority |
| --- | --- |
| `knowledge_support` | Read-only query/context/explain process; no durable mutation or grant creation |
| `knowledge_sink` | Separate, explicitly enabled mutation process; requires an owner-created grant bound to writer, operations, scope, sensitivity, idempotency, and rate/capacity limits |
| `law_support` | Separate read-only process and storage for official and user-private legal evidence |
| CLI | Owner administration, source ingestion, grants, backup, migration, rebuild, forget, and explicit Legal Pack operations |
| Markdown/editor adapters | Open workspace clients; they call reconciliation and shared domain services and do not implement governance |
| Host drivers | Thin Codex/OpenCode/other-host protocol adapters; they do not own retrieval, persistence, identity, or mutation policy |

The process split is a trust boundary, not permission to create independent knowledge engines.
Official and private legal stores remain physically and governably isolated. An MCP boundary is not
an operating-system sandbox; owner-controlled deployment must additionally enforce process identity,
file ownership, ACLs, mount roots, closed child environments, and network/IPC policy appropriate to
the deployment.

### Secret, path, and transcript boundary

Child processes receive only the minimum typed/local projections required for their role. Host or
Provider authentication remains with the exact Host process and is never copied to DeepLaw runners,
scorers, arbiters, receipts, Ledger rows, artifacts, logs, or Provider payloads. `.env` files and
credential paths are not copied, printed, or inherited through ambient environment configuration.

Raw official Host session IDs may enter an owner-controlled enrollment seam only through stdin,
controlled file descriptors, or an owner-only official event. The seam hashes the value immediately;
the raw value never enters argv, persistent state, logs, receipts, or Provider output. DeepLaw does
not read prompts, transcripts, hidden reasoning, or authentication state, and a Host hook/plugin
cannot silently create a grant or durable write.

Provider output is a bounded data projection, not a shell, authority, or identity channel. Paths,
session hashes, internal receipt/selection identities, raw logs, transcript text, reasoning, Secret
material, and rejected or unadmitted content are disclosure failures.

### Legal and source trust

Official catalog/release identity, signatures, trust roots, revocation, monotonic sequence, rollback
protection, immutable releases, historical pinning, and atomic pointer switching are verified before
official material is parsed or activated. User-private material cannot inherit official identity by
filename, URL, mirror, or similarity. Agent interpretation always remains `legal_authority=false`;
DeepLaw supplies evidence and bounded context, not legal applicability, facts, strategy, or a verdict.

## 6. Compatibility, recovery, and change control

Public contract or persistence changes require synchronized schema and migration work, rollback or
recovery behavior, audit/replay/integrity coverage, focused regression tests, and user
documentation. Existing compatibility inputs remain explicitly labelled compatibility/internal and
must not silently become new product roles or public claims.

The canonical backup includes Ledger, Markdown Revision Objects, CAS, staging/conflicts, source
views, manifests, and required capability state under owner-only controls. Derived indexes are
excluded and rebuilt. Forgetting is an explicit lifecycle operation with an owner-confirmed byte
erasure path where policy permits; it must not erase the audit material required for traceability.

No architecture decision in this document is a release, qualification, human-attestation, legal,
competitive, or publication claim. Current machine state, active gate/profile, candidate binding,
and artifact evidence live in the active qualification records and their protocols. This document
must not be used as a second release ledger.

## 7. Non-goals

- generic chunk-RAG, a Markdown converter, an Obsidian replacement, or a transcript warehouse;
- a Host model/runtime, prompt orchestrator, scheduler, browser, shell, or general tool executor;
- a fourth product role, second retrieval engine, independent Legal Pack knowledge engine, or
  duplicated per-Host/per-plane business logic;
- a remote canonical database, multi-tenant SaaS, collaboration control plane, telemetry service,
  or implicit network acquisition;
- ranking, confidence, links, or model output as Authority or permission;
- automatic transcript memory, background session scraping, or complete transcript persistence;
- new databases, Knowledge kinds, Relation predicates, page families, Host adapters, GUI control
  planes, or connectors without the frozen feature-admission and Owner-approval evidence; and
- superiority, SOTA, perfect, complete, RC, or GA claims without the applicable frozen evidence.

## 8. Canonical references

- Product definition and requirements: [`PRODUCT_REQUIREMENTS.md`](PRODUCT_REQUIREMENTS.md)
- Product-to-runtime mapping: [`PRD_TRACEABILITY_MATRIX.md`](PRD_TRACEABILITY_MATRIX.md)
- Autonomous knowledge semantics: [`AUTONOMOUS_KNOWLEDGE_OS.md`](AUTONOMOUS_KNOWLEDGE_OS.md)
- Living Wiki compiler: [`LIVING_WIKI_COMPILER.md`](LIVING_WIKI_COMPILER.md)
- Legal strategy and evidence policy: [`DEEPLAW_2.md`](DEEPLAW_2.md)
- Host integration boundary: [`AGENT_ADAPTERS.md`](AGENT_ADAPTERS.md)
- Evaluation and external qualification: [`EVALUATION_PROTOCOL.md`](EVALUATION_PROTOCOL.md) and
  [`V0_13_QUALIFICATION_PROTOCOL.md`](V0_13_QUALIFICATION_PROTOCOL.md)
- Security policy: [`../SECURITY.md`](../SECURITY.md)
- Historical Pass evidence: immutable `V0_13_PASS*.md` records; consult the relevant record only
  for historical evidence, never as the current architecture or release state.
