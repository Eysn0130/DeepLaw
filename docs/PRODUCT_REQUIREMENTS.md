# DeepLaw Product Requirements

Status: **normative product-direction baseline**  
PRD revision: **1.2**  
Reviewed: **2026-08-08**

This document defines why DeepLaw exists, which user outcomes it owns, the stable product
boundaries, and the evidence required before scope may expand. It is intentionally smaller and
more stable than an implementation specification. Current candidate, release, and qualification
status lives in `docs/V0_13_CORE_SCOPE_DISPOSITION.md`; research snapshots live in
`docs/V0_13_UPSTREAM_RESEARCH.md`.

Runtime facts remain authoritative in `src/deeplaw`, tests, JSON Schemas, SQLite migrations,
`pyproject.toml`, and `uv.lock`. A requirement marked `Target` is not shipped merely because it is
written here. Detailed implementation contracts remain in the canonical subsystem documents named
in `AGENTS.md`.

Normative terms `MUST`, `MUST NOT`, `SHOULD`, and `MAY` describe product requirements. Every future
feature, contract change, or release decision MUST map to a requirement in this document or first
amend this document through the change-control process in section 16.

## 1. Product definition

> DeepLaw is a local-first, host-neutral, evidence-governed Agent Knowledge OS. It compiles
> immutable Source Revisions and governed working knowledge into a human/Agent-readable Living
> Wiki, then delivers the smallest verifiable Knowledge Capsule needed for the current task.

“Knowledge OS” means the local identity, governance, lifecycle, compilation, and context-selection
kernel plus thin drivers. It does not mean an operating system, Agent runtime, scheduler, model
host, or general execution environment.

DeepLaw's primary job is to improve two outcomes:

1. **Task continuity:** a new, forked, concurrent, resumed, or compacted Agent thread can recover
   the correct task-lineage state—goal, confirmed decisions, constraints, verified facts, open
   gaps, next action, and artifact references—without replaying the full conversation or importing
   another thread's state; and
2. **Evidence-grounded shared knowledge:** a human and an Agent can use the same knowledge network
   and traverse a claim back to the exact Source Revision, fragment, locator, and bytes without
   changing Source Authority.

Protected legal and other authoritative materials are a strict evidence policy within this same
Knowledge OS. They are not a separate product and do not redefine DeepLaw as a law-only system.

DeepLaw is a **Source-to-Knowledge and Task-to-Context Compiler**, not a system for storing or
injecting as much history as possible.

## 2. Product thesis

Long context capacity is not the same as reliable context use. Agent quality degrades when current
requirements and decisions compete with stale plans, repeated text, tool logs, failed attempts,
untrusted retrieved instructions, and unrelated source material. Compaction can reduce size but may
also discard or distort important task state. Current Hosts already offer project instructions,
session resume/compaction, and generated cross-session memory. DeepLaw MUST treat those facilities
as complementary: they can improve recall, but they do not by themselves establish portable task
lineage, exact project state, governed evidence, or a hard policy boundary.

DeepLaw therefore treats context as a compiled product:

```text
task intent
  + current admitted Task Checkpoint
  + current governed project knowledge
  + exact evidence required by the task
  + contradiction, exception, temporal and gap challenges
  -> bounded, provider-safe Knowledge Capsule
```

The product succeeds when the Agent takes the correct next action with less context and stronger
evidence. It does not succeed merely because a vector search returned a relevant chunk, a graph has
many edges, a Wiki has many pages, or a benchmark reports high retrieval recall.

> **Primary outcome:** improve correct task continuation and correct evidence use with less
> Provider-visible context than a budget-matched baseline.

Release qualification separately enforces hard safety gates: stale-state admission, false
Authority, secret disclosure, and hidden mutation MUST remain zero in the applicable frozen
hard-failure suites. Provider bytes, latency, RSS, storage, and maintenance cost are guardrails;
they MUST NOT be optimized by sacrificing task correctness, evidence duties, or explicit gaps.

## 3. Problems DeepLaw owns

| ID | Problem DeepLaw owns | Required product response |
| --- | --- | --- |
| `PRD-PROBLEM-001` | New, forked, concurrent, or compacted threads can lose, distort, or cross-contaminate confirmed project state | Recover the correct task-lineage-specific handoff without replaying a transcript |
| `PRD-PROBLEM-002` | Stale plans, logs, duplicates and distractors pollute long context | Compile the minimum context that preserves the next correct action |
| `PRD-PROBLEM-003` | Generic RAG repeatedly reconstructs concepts and relations from raw chunks | Compile reusable knowledge once and refresh affected dependents |
| `PRD-PROBLEM-004` | Humans read files while Agents receive opaque chunks or rows | Share semantic identities through an open Wiki and trusted kernel |
| `PRD-PROBLEM-005` | Evidence, interpretation, confidence and Authority are conflated | Preserve exact Source and label every derivation and limitation |
| `PRD-PROBLEM-006` | Persistent learning can retain injection, false state or secrets | Separate reads/writes and enforce origin, scope, audit and deletion |
| `PRD-PROBLEM-007` | Facts, decisions and sources change over time | Govern current, historical, contested, expired and forgotten state |
| `PRD-PROBLEM-008` | Similar names and memories can collide across projects, Vaults, worktrees, or task branches | Bind every read and write to explicit project and task identity and fail closed on ambiguity |

## 4. Product users and jobs

| User | Primary job | DeepLaw responsibility |
| --- | --- | --- |
| Owner/operator | Control sources, knowledge, grants, lifecycle, backup, and release | Local CLI with explicit, auditable control |
| Agent host | Resume work and obtain task-specific verified context | One bounded read contract, independent of host memory |
| Human knowledge worker | Read, navigate, annotate, and maintain project knowledge | Open Markdown Wiki with stable semantic identity |
| Regulated-domain researcher | Find exact current or historical evidence | Version-, time-, quote-, and locator-correct read path |
| Maintainer/evaluator | Prove safety, quality, compatibility, and release readiness | Frozen protocols, deterministic receipts, reproducible evidence |

The first supported deployment remains local, single-user, and owner-controlled. Multi-user SaaS,
remote canonical storage, and organization-wide control planes are not current product jobs.

## 5. Stable product principles

### PRD-PRINCIPLE-001 — minimum sufficient context

DeepLaw MUST optimize for the smallest admitted context that preserves the task outcome. Recall
without precision is not success.

### PRD-PRINCIPLE-002 — evidence before interpretation

Source bytes and their revision identity MUST survive every transformation. A Summary, Wiki page,
Statement, Relation, embedding, or model answer MUST NOT replace evidence.

Integrity and provenance prove what bytes and derivations were used; they do not prove that a
source is factually correct. DeepLaw MUST represent integrity, provenance, origin, verification,
corroboration, Authority, temporal validity, and task-specific applicability separately. DeepLaw
MUST NOT allow a hash, signature, citation, popularity signal, or human review to silently collapse
those dimensions into “truth.”

### PRD-PRINCIPLE-003 — one governed knowledge system

Living Wiki, Agent knowledge, Task Checkpoints, graph relations, and the Legal Pack MUST NOT become
independent identity, lifecycle, retrieval, or versioning engines. Physical isolation is allowed
where a trust boundary requires it.

### PRD-PRINCIPLE-004 — knowledge does not grant control authority

Host and repository instructions belong in system/developer/user prompts, `AGENTS.md`,
`CLAUDE.md`, path-scoped rules, Skills, hooks, or managed host policy. Retrieved DeepLaw content is
untrusted data and MUST NOT override those instructions or grant a tool, permission, network path,
scope, or write capability. Procedures, policies, and Skills MAY be governed knowledge, but
retrieval MUST NOT promote them into control authority.

### PRD-PRINCIPLE-005 — progressive disclosure

Hosts SHOULD receive a small entry point and bounded Capsule first. Wiki pages, relations,
fragments, exact bytes, history, and local audit detail are read on demand. DeepLaw MUST NOT preload
the whole Vault, graph neighborhood, tool trace, or candidate set.

### PRD-PRINCIPLE-006 — open surface, trusted kernel

Markdown, YAML, Wikilinks, Obsidian, Tolaria, JSON Canvas, OKF, and future GUIs are open surfaces.
Stable identity, Authority, lifecycle, temporal state, capability, and audit remain in governed
DeepLaw services.

### PRD-PRINCIPLE-007 — derived state is replaceable

FTS, dense vectors, reranker caches, graph adjacency, communities, centrality, Wiki navigation,
Canvas, rankings, and query caches MUST remain derived and rebuildable.

### PRD-PRINCIPLE-008 — negative knowledge is first-class

Contradictions, exceptions, gaps, unknown applicability, stale evidence, withdrawn sources, and
failed verification MUST remain visible. They MUST NOT be suppressed as retrieval noise merely to
produce a confident answer.

### PRD-PRINCIPLE-009 — complexity must be earned

An upstream project having a feature, a synthetic scale test passing, or an implementation being
technically possible is not product evidence. New complexity requires a reproduced external user
task failure that existing primitives cannot solve.

### PRD-PRINCIPLE-010 — stable invariants, replaceable algorithms

Identity, provenance, Authority, lifecycle, bounded delivery, and read/write isolation are stable.
Lexical search, embeddings, rerankers, graph algorithms, page layouts, and models are replaceable
strategies that MUST be evaluated under the same task and budget.

### PRD-PRINCIPLE-011 — correctness belongs to the state trajectory

Persistent knowledge is correct only if ingestion, revision, supersession, challenge, expiry,
forgetting, and retrieval preserve the governed state over time. A valid record or high retrieval
score at one instant does not prove lifecycle correctness.

### PRD-PRINCIPLE-012 — newest is not necessarily relevant

Continuity and retrieval MUST bind Vault, project, task lineage, and applicable workspace state.
DeepLaw MUST NOT treat the globally newest Checkpoint, memory, file, relation, or audit event as the
correct context merely because it is recent.

## 6. Product architecture boundary

DeepLaw has two durable policy planes and one non-canonical delivery plane:

```text
Durable plane A: immutable evidence
  Source bytes -> Source Revision -> Fragment -> Locator -> parse provenance

Durable plane B: governed compounding knowledge
  Knowledge identity -> Knowledge Revision -> Relation Revision
  Task Run/Checkpoint lineage -> Ledger event/current pointer

Ephemeral delivery plane
  discovery/index/cache -> admission -> challenge -> selection -> bounded Capsule/receipt
```

The delivery plane is not a third Authority domain. Query traces and caches are not Canonical
Knowledge.

The Knowledge OS kernel is limited to four responsibilities:

1. **Identity and provenance:** preserve what a Source or Knowledge Revision is and where it came
   from;
2. **Policy and lifecycle:** enforce origin, Authority, verification, scope, sensitivity, time, and
   capability;
3. **Compilation and reconciliation:** turn sources or admitted proposals into deterministic,
   recoverable revisions; and
4. **Context compilation:** select a bounded, verifiable, task-specific Capsule and explain gaps.

CLI, MCP, editors, Wiki projections, indexes, model-assisted planners, and Host adapters are
drivers around this kernel. They MUST NOT duplicate its business rules.

## 7. Required product workflows

### 7.1 Cross-thread continuity

At a meaningful task boundary, an Agent or human MAY propose a bounded Task Checkpoint. A
Checkpoint uses the existing governed `memory` capability; it is not a new top-level Authority or
an unrestricted transcript store.

A current Checkpoint MUST be able to express the current goal, confirmed decisions, constraints,
verified facts, open gaps, next action, and relevant Artifact references. Exact record names and
closed enums are versioned runtime contracts, not permanent product taxonomy.

`PRD-CONT-001` Every Checkpoint MUST be scope- and sensitivity-admitted, current, TTL- or
lifecycle-bounded, and attributable to its writer. An Agent-generated Checkpoint MUST bind to an
immutable successful Run before normal continuity retrieval. A human-authored Checkpoint MAY use
an explicit human provenance path and MUST NOT be falsely represented as Agent-run evidence.

`PRD-CONT-002` A Checkpoint MUST NOT contain a full chat, hidden reasoning, raw tool logs,
credentials, capability tokens, or private absolute paths.

`PRD-CONT-003` A superseded, expired, forgotten, quarantined, target-mismatched, or unbound
Checkpoint MUST NOT influence current task context.

`PRD-CONT-004` A Checkpoint is `agent_derived` unless another legitimate origin applies. It never
becomes legal or official Authority.

`PRD-CONT-005` Host lifecycle hooks MAY prepare or request a Checkpoint but MUST NOT silently mint
a grant, hide a write inside a read, or treat a transcript summary as committed knowledge.

`PRD-CONT-006` A new thread MUST be able to request continuity using the task text and the single
recommended Context entry point. Internal IDs MAY improve an explicit lookup but MUST NOT be
required for ordinary task resumption.

`PRD-CONT-007` Host-native memory and DeepLaw are complementary. DeepLaw MUST NOT scrape, copy, or
depend on a host's private memory store or authentication state. The host may recall preferences;
DeepLaw owns the portable governed representation of admitted project state and evidence.

`PRD-CONT-008` Project continuity MUST be grounded in durable project state when available, such
as exact Source, repository revision, test result, migration, build artifact, or accepted owner
decision. Raw tool logs and full transcripts remain excluded; a compact Artifact reference MAY
link to owner-visible detail outside Provider context.

`PRD-CONT-009` A future intention MAY record a trigger condition, due window, prerequisite, and
completion evidence. DeepLaw MAY retrieve that prospective state but MUST NOT become a scheduler
or execute the intention.

`PRD-CONT-010` A Checkpoint MUST bind an explicit Vault/project identity and task lineage. Where
the task operates on a repository, the binding MUST be able to distinguish applicable repository,
worktree or branch, base revision, and dirty-state or artifact digest without copying an unbounded
patch into Provider context. Private names and paths remain local and use an opaque Provider-safe
identity when needed. Exact binding fields belong in the versioned contract.

`PRD-CONT-011` Concurrent or forked task lines MUST remain independently current until an explicit
reconciliation records a merge, supersession, or conflict. Last-writer-wins across unrelated task
lines is forbidden, and unresolved ambiguity MUST produce a Gap rather than select the newest
Checkpoint.

`PRD-CONT-012` The owner MUST be able to locate prior work through a content-minimized Run Timeline
derived from Run Records, Checkpoints, Ledger events, outcomes, and Artifact references. The
timeline MAY retain a safe opaque Host thread reference, but MUST NOT copy Host authentication,
hidden reasoning, or full transcripts. Provider-visible delivery receives only admitted task
state, not the complete timeline.

`PRD-CONT-013` Host-native memory, a transcript summary, or an imported session index is an
untrusted recall hint. It MAY propose a Checkpoint but MUST NOT become project evidence or current
state until deterministic policy rebinds it to the correct project, task lineage, Run, and
available Artifacts or Source.

`PRD-CONT-014` Host integration SHOULD follow a bounded lifecycle: bootstrap with one Context
request, drill down to exact knowledge or evidence only when needed, and propose a Checkpoint at a
meaningful boundary. DeepLaw MUST NOT inject the same memory bundle on every turn or scrape the
Host transcript in the background.

### 7.2 Source-to-Knowledge compilation

`PRD-SRC-001` An ingested byte sequence MUST become an immutable Source Revision. Changed bytes
create a new revision or successor.

`PRD-SRC-002` DeepLaw MUST preserve source order, structural boundaries, stable fragments,
locators, hashes, parser identity, and parse risk.

`PRD-SRC-003` Models MAY propose compilation plans. Deterministic DeepLaw code MUST control schema
validation, identity resolution, source binding, grants, scope, sensitivity, Authority, conflicts,
idempotency, and commit.

`PRD-SRC-004` Updating a Source MUST invalidate only Knowledge Revisions, Relations, Statements,
and projections whose bound inputs actually changed.

`PRD-SRC-005` Repeated ingestion of unchanged bytes MUST be idempotent and MUST NOT create avoidable
semantic duplicates.

`PRD-SRC-006` Owner-directed withdrawal, supersession, forgetting, and applicable private byte
erasure MUST be explicit lifecycle operations; immutability MUST NOT deny an authorized deletion
path.

`PRD-SRC-007` A compiled Wiki or Knowledge set is a lossy projection unless coverage is proven.
Compilation MUST bind input revisions, expose coverage and unresolved gaps, and support bounded
diagnostic probes that can identify omitted or distorted task-critical facts.

`PRD-SRC-008` A compilation failure MUST trigger targeted, dependency-aware refinement or an
explicit Gap. It MUST NOT be hidden by generic regeneration, unlimited source injection, or a
claim that the Wiki is a lossless substitute for Source.

`PRD-SRC-009` Source discovery and acquisition MUST be bounded by an owner-visible root or
allowlist, exclusions, acquisition purpose, and snapshot manifest. DeepLaw MUST NOT recursively
discover unrelated repositories, home-directory content, browser state, mail, or cloud sources
merely because a Host can access them.

`PRD-SRC-010` A connector or importer is an acquisition adapter only. It MUST preserve source
identity, exact acquired bytes or a verifiable immutable snapshot, acquisition provenance, and
scope; connector authentication, filenames, URLs, or provider trust MUST NOT create Authority.

### 7.3 Governed knowledge growth

`PRD-KNOW-001` Durable Knowledge writes MUST create a new revision and audit event rather than
silently rewriting history.

`PRD-KNOW-002` Agent writes MUST pass through the separate `knowledge_sink` process and an
owner-created grant bound to writer, operations, scope, sensitivity, rate/capacity, and
idempotency.

`PRD-KNOW-003` `knowledge_support`, Context, Query, Wiki reads, and `law_support` MUST NOT perform
hidden persistent writes.

`PRD-KNOW-004` DeepLaw SHOULD update an existing semantic identity when appropriate, but MUST
preserve contested identities, wrong-merge evidence, and explicit split/merge lineage.

`PRD-KNOW-005` Origin, verification, Authority, lifecycle, epistemic state, scope, sensitivity,
writer, valid time, and transaction time MUST remain separate dimensions.

`PRD-KNOW-006` Model confidence, embedding similarity, link count, graph centrality, community,
feedback frequency, and usage count MUST NOT grant Authority, permission, or wider scope.

`PRD-KNOW-007` Consolidation MUST preserve source and revision lineage, contradictions, and owner
deletion semantics. It MUST NOT replace several uncertain memories with one falsely certain
summary.

`PRD-KNOW-008` Outcome feedback MAY evaluate whether admitted context helped a task, but feedback
MUST NOT automatically promote Authority, verification, scope, sensitivity, or capability.

`PRD-KNOW-009` DeepLaw MUST measure and bound maintenance debt: duplicate identities, stale or
unsupported syntheses, orphaned objects, broken relations, invalidation fan-out, and unnecessary
recompilation. Knowledge growth without lifecycle maintenance is not product success.

`PRD-KNOW-010` Vault and project boundaries MUST be explicit, stable, and fail closed. Knowledge
with the same title, alias, semantic key, or embedding neighborhood in another Vault or project
MUST NOT be merged, admitted, or mutated without an explicit cross-boundary reference and policy.
Independent knowledge bases MUST remain independently queryable, backupable, forgettable, and
portable.

### 7.4 Living Wiki for human/Agent co-reading

`PRD-WIKI-001` DeepLaw MUST project committed Source and Knowledge identities into readable
Markdown pages with explicit Evidence, Interpretation, Authority, freshness, limitation, and gap
labels.

`PRD-WIKI-002` Humans and Agents MUST be able to traverse the same semantic link network, although
their presentation formats MAY differ.

`PRD-WIKI-003` File paths, filenames, titles, aliases, frontmatter, Wikilinks, backlinks, and
Canvas nodes MUST NOT independently establish stable identity, Authority, or permission.

`PRD-WIKI-004` Rename and move MUST preserve semantic identity. Human content edits become new
revisions through reconciliation.

`PRD-WIKI-005` Page Registry, Link Index, and Resolver MUST provide bounded lookup without a warm
query scanning the entire filesystem.

`PRD-WIKI-006` Full and incremental rebuilds over the same canonical inputs MUST be semantically
equivalent and MUST preserve user files not owned by a verified projection manifest.

`PRD-WIKI-007` Per-object Canvas, community pages, guided tours, codemaps, relation-path views, and
similar navigation features are optional derived views. They enter the default product only after
a human/Agent task proves that existing pages and links are insufficient.

`PRD-WIKI-008` Obsidian and Tolaria are editor clients. A future GUI MUST consume the same domain
services and MUST NOT become a second control plane.

`PRD-WIKI-009` Humans and Agents share readable projections and semantic addresses, not equal
Authority. Direct editor changes to governed content MUST enter reconciliation as attributable
revision proposals; editor writes MUST NOT mutate protected Source or bypass policy.

`PRD-WIKI-010` Every governed page projection MUST expose enough machine-readable identity to
resolve its semantic object and current revision and enough human-readable status to distinguish
origin, Authority, lifecycle, valid time, freshness, evidence, interpretation, limitation, and Gap.
Exact frontmatter keys and Schema versions belong in versioned contracts.

`PRD-WIKI-011` A Wikilink or backlink is navigation evidence, not a semantic assertion. Only a
validated typed Relation Revision may assert `supports`, `contradicts`, `depends_on`, temporal, or
other governed semantics. Editors and compilers MUST NOT promote co-occurrence or a plain link into
a typed relation without an attributable proposal and validation.

`PRD-WIKI-012` The workspace MUST distinguish protected Source projections, DeepLaw-owned derived
projections, governed editable knowledge, and user-owned files. Each class MUST have explicit edit,
reconciliation, regeneration, and deletion behavior; a projection rebuild MUST NOT overwrite or
delete user-owned material.

`PRD-WIKI-013` Humans and Agents MUST be able to request a bounded on-demand semantic neighborhood
or typed relation path with revision, provenance, temporal state, hop budget, truncation, and Gap
information. Pre-generating a Canvas or path page for every object remains optional derived
presentation, not the graph contract.

### 7.5 Context compilation and delivery

The required pipeline is:

```text
Discovery
-> Admission
-> task/duty-aware rerank
-> contradiction, exception, temporal and freshness challenge
-> targeted evidence fallback
-> deduplication
-> bounded selection
-> provider-safe projection
```

`PRD-CTX-001` `deeplaw knowledge context` and its equivalent Python/MCP domain seam are the only
recommended Agent context product. `knowledge query` is the operator diagnostic seam.

`PRD-CTX-002` Discovery channels propose candidates only. Admission MUST re-check source integrity,
scope, sensitivity, lifecycle, valid time, Authority intent, and task purpose before delivery.

`PRD-CTX-003` Selection MUST operate under explicit item, source, character, token, graph-hop, and
payload budgets. Provider-visible output MUST have a hard byte bound.

`PRD-CTX-004` The Provider receives only admitted Task Checkpoint state, task-relevant Statements,
minimum Source/Citation, Authority, Verification, Freshness, Limitation, Contradiction, Exception,
Gap, and an opaque `receipt_id`.

`PRD-CTX-005` The Provider MUST NOT receive rejected-candidate text, complete scores, SQL, cache or
parser diagnostics, raw OCR logs, an entire graph neighborhood, duplicate fragments, secrets,
private paths, full sessions, or hidden reasoning.

`PRD-CTX-006` A fallback from compiled knowledge to raw evidence MUST remain bounded and visible in
the Query Plan, explanation, gap, or receipt.

`PRD-CTX-007` Missing, unverifiable, contested, temporally incomplete, or out-of-scope evidence
MUST produce an explicit Gap. DeepLaw MUST NOT silently substitute model memory or Web content and
label it official.

`PRD-CTX-008` Query Trace MAY exist only as bounded, TTL-controlled, rotating, redacted, integrity-
checked local state with an owner deletion path. It is not a canonical mutation and SHOULD NOT
store query plaintext or Source bodies by default.

`PRD-CTX-009` Receipts MUST make the selected revision and evidence identities re-resolvable for
local audit without expanding the Provider payload.

`PRD-CTX-010` Retrieval quality MUST be measured by downstream task success, evidence duties, and
context efficiency, not Recall@K alone.

`PRD-CTX-011` Host and transport sessions are not durable knowledge identity. Every Context
request and receipt MUST carry explicit version, scope, task intent, budgets, and truncation/gap
semantics sufficient for stateless invocation and idempotent retry.

`PRD-CTX-012` Host capability discovery SHOULD be machine-readable and MUST distinguish read-only
Context, owner diagnostics, and granted mutation. Capability negotiation is an adapter contract;
it does not create a second Knowledge protocol or widen a grant.

`PRD-CTX-013` Physical row order, file order, import order, identifier order, or corpus position
MUST NOT decide candidate eligibility. Bounded indexes and candidate pools MUST preserve
position-independent retrieval; every resource-bound cutoff or tail omission MUST be explicit in
the plan, receipt, and Gap rather than hidden behind a fixed prefix scan.

`PRD-CTX-014` Every Capsule MUST bind the exact Vault/project, the applicable task lineage or its
explicit absence, input audit head, selected Source and Knowledge Revisions, and selection policy
used to resolve it. If relevant state changes before reuse, the Host MUST re-resolve or receive an
explicit stale-state/changed-head Gap; a prior Capsule is an immutable snapshot, not a live truth
pointer.

`PRD-CTX-015` A Context request that cannot distinguish among eligible Vaults, projects, task
lines, entities, or temporal versions MUST fail closed with bounded disambiguation candidates from
the already-admitted scope only. It MUST NOT search or reveal a broader private scope or choose the
highest similarity result silently.

### 7.6 Protected and legal evidence

`PRD-EVID-001` Protected source material remains evidence. Humans and Agents MUST NOT edit an
existing Source Revision in place. An authorized owner MAY add a successor, withdraw, revoke, or
perform an applicable deletion operation.

`PRD-EVID-002` `law_support` remains a separate read-only process and store boundary inside the
shared Knowledge OS semantics.

`PRD-EVID-003` Official and user-private legal stores MUST remain physically and governably
isolated. A filename, URL, mirror, model statement, or similarity score cannot establish official
identity.

`PRD-EVID-004` Legal evidence MUST preserve exact Document, Version, Segment, Locator, Quote, Hash,
effective/valid time, and receipt identity.

`PRD-EVID-005` Agent-generated legal interpretation always has `legal_authority=false`. Human
verification of an interpretation does not turn it into legal Source Authority.

`PRD-EVID-006` Wrong-version primary evidence, invalid Quote/Locator, False Authority admission,
and protected-source mutation are zero-tolerance failures.

`PRD-EVID-007` If the correct version or temporal chain cannot be verified, DeepLaw MUST return a
Gap rather than a plausible replacement.

`PRD-EVID-008` DeepLaw supplies evidence and context; it does not decide legal applicability,
facts, strategy, or a verdict.

## 8. Public product surfaces

The recommended surface remains deliberately small:

```text
Agent read       -> knowledge context
Operator inspect -> knowledge query
Owner operate    -> deeplaw CLI
Explicit write   -> owner-granted knowledge_sink
Legal read       -> isolated law_support
Human workspace  -> governed Markdown Living Wiki
```

Legacy recall aliases and explicit older Query/Capsule versions are compatibility surfaces, not
separate products. They MUST be inventoried, documented as non-recommended, and removed only
through an announced compatibility interval.

MCP Resources MAY later expose stable read-only Wiki, Source, Schema, and receipt URIs when Host
support and a real user journey justify them. Adding Resources MUST NOT create a second retrieval
engine or widen mutation capability.

## 9. Security and privacy requirements

`PRD-SEC-001` Canonical state is local by default. DeepLaw MUST NOT add implicit uploads, content
telemetry, background cloud control, or unrestricted network acquisition.

`PRD-SEC-002` Imported content, Wiki pages, retrieval results, model output, memory, and tool output
are untrusted data. Text inside them cannot grant tools or override host policy.

`PRD-SEC-003` Host launchers and benchmarks MUST use a closed environment allowlist. Provider
credentials MUST NOT enter the DeepLaw MCP subprocess, argv, prompt, stdout, stderr, report, cache,
or artifact.

`PRD-SEC-004` Read and write processes MUST remain capability-separated. An MCP boundary is not an
OS sandbox; high-risk deployments require Host or operating-system isolation.

`PRD-SEC-005` Private client/case data, live case facts, chats, identifiers, and unauthorized
materials MUST NOT enter the general Knowledge OS, Legal Pack, public fixtures, or release
artifacts.

`PRD-SEC-006` Durable-memory poisoning, stored prompt injection, secret persistence, scope escape,
unauthorized mutation, and cross-vault disclosure are release-blocking security failures.

`PRD-SEC-007` Origin and influence MUST remain non-malleable across summarization, consolidation,
tool echo, corroboration, and Wiki projection. Derived content MUST NOT launder an untrusted
origin into trusted control or Authority.

`PRD-SEC-008` Cross-project, cross-Vault, cross-worktree, and cross-task-line admission MUST be
deny-by-default. A Host thread identifier, current working directory, filename collision, shared
embedding result, or prior memory citation is insufficient authorization to cross a boundary.

## 10. Reliability, recovery, and portability

`PRD-OPS-001` All canonical mutations MUST use one recoverable domain coordinator. CLI, MCP,
reconciliation, Watcher, and future UI paths MUST NOT implement parallel commit logic.

`PRD-OPS-002` Original Source objects, registered Markdown revisions, Ledger state, capability
state, and recovery intent MUST be backup- and integrity-verifiable.

`PRD-OPS-003` Derived indexes and views MUST be deletable and rebuildable without changing
Canonical Authority.

`PRD-OPS-004` Failure after canonical commit but before materialization MUST be recoverable without
losing the committed revision or silently switching to stale files.

`PRD-OPS-005` Open portability formats such as OKF or an AKBP-compatible profile MAY be export and
import projections only. They MUST NOT replace DeepLaw identity, Ledger, capability, or legal trust
semantics.

`PRD-OPS-006` Every export MUST bind exact artifact hashes and omit secrets, capability tokens,
unauthorized content, and private absolute paths.

`PRD-OPS-007` Forgetting and authorized erasure MUST be testable at the semantic and storage
boundaries: forgotten content is not normally retrievable, dependent projections are updated,
and any retained tombstone reveals no deleted private payload.

`PRD-OPS-008` The owner MUST be able to inspect revision history and restore prior governed
content by creating a new attributable current revision or recovery event after conflict and
dependency validation. Restore MUST NOT rewind the audit head, mutate historical Source bytes, or
silently repoint one revision while leaving dependents inconsistent.

`PRD-OPS-009` The owner MUST have a bounded, integrity-verifiable event and revision timeline for
Source acquisition, Knowledge mutations, reconciliation, compilation, selection receipts,
withdrawal, forgetting, restore, and recovery. Provider-visible context MUST NOT receive this full
operational history or hidden model reasoning.

## 11. Success measures

Thresholds and budgets are frozen in the applicable Evaluation Protocol before holdout results are
read. This PRD defines the required outcome families, not mutable benchmark tuning constants.

### 11.1 Task continuity

- First Correct Action;
- Decision Preservation;
- Useful Context Recall;
- RelevantChars / ContextChars;
- Stale Decision Inclusion;
- False Memory Admission;
- Contradiction/Gap Coverage;
- time to correct resumption;
- Provider bytes, latency, RSS, and storage;
- comparison against the same Host without DeepLaw under the same tools and budget;
- action, tool, and parameter correctness when recalled state is used;
- update, supersession, prospective-trigger, abstention, and forgetting correctness;
- task-lineage and workspace identity precision under concurrent threads and worktrees;
- cross-thread state contamination and conflict-detection rate; and
- owner time-to-locate a prior Run, decision, outcome, or Artifact without transcript replay.

Stale Decision Inclusion, False Memory Admission, and cross-task-line contamination are
zero-tolerance for qualification tasks.

### 11.2 Living Wiki

- human task completion and time-to-source;
- Agent task completion;
- exact Source Revision/fragment/locator recovery;
- identity precision across rename, move, aliases, and same-name entities;
- broken, orphaned, dangling, and unauthorized link counts;
- dependent-only invalidation;
- full/incremental rebuild semantic equivalence;
- compilation coverage and task-critical omission rate;
- targeted refinement recovery without unrelated regeneration;
- typed-Relation precision versus plain Wikilink/navigation edges;
- edit/reconciliation correctness across protected, derived, governed, and user-owned pages; and
- bounded relation-path completeness with explicit truncation.

### 11.3 Context quality

- Recall@K, Precision@K, MRR, and nDCG at the discovery seam;
- Useful Context Recall and Duty Coverage at the selection seam;
- RelevantChars / ContextChars;
- Redundancy and Duplicate Evidence Rate;
- False Suppression Rate;
- Distractor-induced Answer Delta;
- physical-order and tail-position recall invariance;
- stale-head detection and disambiguation correctness;
- token savings and Provider bytes; and
- end-task correctness.

Contradictions, exceptions, temporal uncertainty, and gaps are required context when relevant and
MUST NOT be scored as noise.

### 11.4 Protected evidence

- Document and Exact Segment Recall;
- Target Identity Precision;
- Definition/Exception/Proviso/Cross-reference Recall;
- Temporal Correctness;
- Citation and Locator validity;
- Correct Gap Precision/Recall;
- Wrong-version Inclusion; and
- False Authority Admission.

False Authority, wrong-version primary evidence, and invalid Quote/Locator primary evidence MUST
remain zero.

### 11.5 Security and operations

- unauthorized persistent mutations: zero;
- secret, capability, restricted-data, and private-path disclosure: zero;
- memory-poisoning persistence from untrusted content: zero in the frozen attack suite;
- write-to-retrieve-to-act poisoning success: zero in the frozen attack suite;
- cross-Vault, cross-project, cross-worktree, and cross-task-line disclosure: zero;
- selective forgetting with benign-knowledge preservation;
- semantic restore correctness without audit rewind or dependent-state inconsistency;
- rebuild and audit integrity;
- bounded warm-read behavior;
- reproducible artifacts and environment provenance;
- duplicate/stale/orphan debt, invalidation fan-out, and recompilation cost; and
- capability-specific qualification status rather than one undifferentiated product score.

## 12. Current-status boundary

This PRD does not carry mutable release status, package version, benchmark thresholds, or a
candidate-specific checklist. The authoritative current disposition is
`docs/V0_13_CORE_SCOPE_DISPOSITION.md`; thresholds and holdout rules live in the applicable
evaluation protocol. A capability is not shipped, qualified, or claim-eligible merely because its
target appears here.

## 13. Explicit non-goals and frozen scope

DeepLaw is not:

- a generic RAG or document-chunk application;
- a chat-history or complete-transcript archive;
- a replacement for Codex/Claude/OpenCode memory, compaction, Projects, or session control;
- a whole-life personal profile, screen/activity surveillance system, or autonomous personal
  assistant memory;
- an Agent runtime, model host, prompt orchestrator, browser, shell, or general tool executor;
- an Obsidian or Tolaria replacement;
- a graph database product;
- a remote canonical database, multi-tenant SaaS, or team control plane;
- an automatic legal adjudicator; or
- a system in which generated popularity, confidence, usage, or links create Authority.

The following remain out of the current scope freeze unless an external user task passes the
feature-admission gate:

- default generated Guides, codemaps, materialized relation-path pages, Obsidian Bases, and new
  page families; bounded typed-relation traversal remains core;
- new Knowledge kinds, Relation predicates, Authority dimensions, or persistent databases;
- per-object Canvas, default communities, centrality, or graph visualization expansion;
- complete materialized `as_of` Wiki browsing and dependency-blind pointer rewind or
  single-Revision revert; temporal retrieval and semantic restore by new revision remain core;
- new Host adapters and Host-specific business logic;
- automatic full-transcript ingestion or unrestricted auto-memory;
- broad Web, Gmail, Notion, social, or enterprise connector acquisition;
- cloud canonical storage, collaboration, and multi-tenancy; and
- competitive, SOTA, complete, perfect, or leadership claims without reproducible named-comparator
  evidence.

Existing compatibility behavior is not deleted by this PRD. It is frozen, simplified, or
deprecated through an explicit migration rather than expanded as a new product line.

## 14. Feature-admission and complexity gate

Before implementation, every proposed feature MUST supply a Product Task Card answering:

1. Which `PRD-*` outcome does it improve?
2. What repository-external human or real-Host task currently fails?
3. What is the frozen reproduction and success metric?
4. Why can the failure not be solved with an existing Source, Knowledge Revision, Relation,
   Ledger, Wiki, Context, receipt, or lifecycle primitive?
5. Which public command, MCP operation, Schema, table, kind, predicate, page family, process,
   dependency, or Host surface would be added?
6. What Authority, privacy, migration, recovery, payload, and compatibility risks change?
7. What simpler alternative was rejected, and with what evidence?
8. What is the deletion, deprecation, or rollback path if the feature does not improve the held-out
   task?

Missing evidence for questions 2 through 4 means **do not implement**.

The default complexity budget is:

| Surface | Default product budget |
| --- | ---: |
| Recommended Agent context entry points | 1 |
| Recommended operator query entry points | 1 |
| Hidden write paths | 0 |
| Source in-place mutation paths | 0 |
| Ranking-to-Authority paths | 0 |
| Host Adapter business-logic copies | 0 |
| New stores without a measured trust or scale requirement | 0 |
| Default full-transcript persistence | 0 |

Any change to Knowledge shape, Relation semantics, Ledger governance, public CLI/MCP behavior,
Legal trust, or persistent capability MUST include synchronized Schema, migration/recovery,
compatibility, audit/integrity coverage, tests, and user documentation.

## 15. Capability maturity and evidence order

Continuity/Context, Living Wiki, protected/legal evidence, and Host/portability adapters share one
kernel but qualify independently. A domain-specific failure does not prove another capability
failed; a shared-kernel safety failure blocks every affected capability.

Use these labels without implication:

- `Target`: required here but not proven in runtime;
- `Implemented`: present in an exact candidate with contract and regression evidence;
- `Qualified`: passed frozen repository-external Human Gold or real-Host tasks plus applicable
  security and operational gates; and
- `Released`: delivered in a verified artifact with accurate user documentation.

Work proceeds in this evidence order:

1. prove each core user outcome against the simplest budget-matched baseline;
2. repair only reproduced root causes, then use a fresh unseen holdout;
3. simplify duplicate entry points, compatibility surfaces, and unused product concepts;
4. qualify scale, recovery, portability, platform, and artifact reproducibility for the exact
   capabilities being claimed; and
5. evaluate richer retrieval, graph, editor, or attestation strategies only after a frozen task
   proves simpler primitives insufficient.

Current candidate gates and their status remain in the disposition and evaluation documents, not
in this stable PRD.

## 16. PRD change control and traceability

- A change to the product definition, primary outcome, policy planes, Authority boundary, or
  non-goals requires an Owner-approved major PRD revision.
- A new user outcome or product surface requires a minor PRD revision and an ADR when the decision
  is consequential and difficult to reverse.
- Clarifications that do not change scope use a patch revision.
- Implementation PRs MUST cite one or more `PRD-*` requirements and the relevant failing user task.
- Release acceptance matrices MUST map each in-scope requirement to runtime code, public contract,
  tests, external evidence, status, and limitations.
- Research reports and upstream feature matrices inform decisions but do not authorize scope.
- Exact enums, field names, limits, TTLs, and wire shapes live in versioned contracts or subsystem
  specifications; this PRD preserves their required semantics without freezing incidental syntax.
- Repository execution guidance MUST remain concise and MUST NOT duplicate this document. Adoption
  of this PRD in an instruction file that is also a frozen evaluation input requires an explicit
  protocol-version change; until then, implementation Task Cards MUST cite this path directly.
- When PRD and runtime differ, record the difference as `Current`, `Target`, `Deferred`, or
  `Not Implemented`. Documentation MUST NOT silently claim the target is shipped.

## 17. Strategic differentiation

DeepLaw's differentiation target is trust and context quality, not the largest feature list.
Leadership, superiority, or competitive eligibility requires separate reproducible evidence.

| Differentiator | Product meaning |
| --- | --- |
| Evidence-governed Context Compiler | Exact Source identity, governed revisions, duty-aware selection and bounded delivery form one auditable chain |
| Host-neutral Context Contract | Codex, Claude Code, OpenCode and future Hosts consume the same state without sharing login, transcript or private memory |
| Branch-safe Task State Ledger | Concurrent threads and worktrees retain separate Run/Checkpoint lineages and reconcile conflicts explicitly instead of sharing one latest memory |
| Content-minimized Run Timeline | Owners can locate prior work and Artifacts without turning the Knowledge OS or Provider context into a transcript warehouse |
| Safe compounding knowledge | Agents propose; deterministic policy controls identity, Authority, scope, lifecycle, commit and deletion |
| Human-readable, machine-governed Wiki | People and Agents share Markdown while the Ledger, not paths or frontmatter, governs identity |
| Verifiable negative state | Contradiction, gap, staleness, withdrawal, uncertainty and out-of-scope state remain first-class |
| Replayable context selection | A compact Capsule and receipt let the owner re-resolve the exact selected revisions and evidence |
| Knowledge Compilation CI | Dependency-aware invalidation, coverage probes and targeted refinement check Wiki compilation |
| Origin-bound lifecycle security | Influence remains bound to origin through summarize, retrieve, act, repair and forget |

These differentiators reuse the existing DeepLaw kernel. They are not authorization to add new
databases, graph engines, Agent runtimes, or UI frameworks.

## 18. Research boundary

Research informs falsifiable requirements; it does not authorize a feature, dependency, status, or
quality claim. The durable conclusions are:

- Host instructions, resume/compaction, local generated memories, Projects, and subagents are
  increasingly capable complementary Host facilities. DeepLaw does not compete with them for
  transcript or preference recall; it supplies portable, task-lineage-specific governed project
  state and evidence.
- LLM Wiki compilation can reduce repeated derivation but is not lossless; coverage probes and
  exact-evidence fallback are product requirements.
- Markdown, Wikilinks, Obsidian, Tolaria, OKF, and AKBP are open editor or interchange surfaces,
  not DeepLaw identity, Authority, or capability systems.
- Memory correctness is a governed state trajectory and must be evaluated through update, use,
  action, and forgetting rather than retrieval alone.
- Multi-session memory must also be evaluated under interdependent tasks, concurrent branches,
  stale corrections, wrong-target updates, and cross-project isolation; final-answer recall alone
  is insufficient.
- MCP is a transport and capability surface. DeepLaw continuity and identity remain explicit and
  stateless with respect to a Host connection.
- Provenance and attestation can prove origin or sanctioned computation; they cannot alone prove
  factual truth or task applicability.
- Personal-memory layers and temporal context graphs demonstrate useful extraction, consolidation,
  and historical retrieval patterns, but do not justify turning DeepLaw into an Agent runtime,
  personal-profile service, graph database product, or automatic transcript ingester.

Frozen upstream commits, licenses, primary references, and current candidate consequences live in
`docs/V0_13_UPSTREAM_RESEARCH.md`. Broader comparisons live in
`docs/UPSTREAM_CAPABILITY_MATRIX.md`. Neither document expands product scope.

## 19. Final product decision

DeepLaw's future is not a larger RAG, a transcript warehouse, an Obsidian clone, a universal graph,
or an Agent runtime. Its durable direction is:

> preserve exact evidence; compile governed knowledge; maintain explicit project, task-lineage,
> revision, time, conflict, and Authority state; let humans and Agents share an open Wiki; and
> deliver only the minimum verified context needed for the next correct action.

If a proposed feature does not materially improve that chain under an external task and a frozen
budget, it is outside the product core.
