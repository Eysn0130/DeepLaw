# DeepLaw Knowledge OS

Status: **v0.7 compatibility baseline retained by v0.12.0**, reviewed 2026-07-30. The current autonomous
contract is [`AUTONOMOUS_KNOWLEDGE_OS.md`](AUTONOMOUS_KNOWLEDGE_OS.md); where this document requires
per-item human review or describes Markdown as projection-only, it records the legacy path rather
than the current default for Agent-derived knowledge. Runtime behavior is defined by code, tests,
JSON Schemas, SQLite migrations, and the lockfile—not by this document. Competitive model/baseline
evidence remains pending and does not change the commercial runtime contract.

## Product boundary

DeepLaw is permanently a local, single-user Agent Knowledge OS. It compiles durable, verifiable,
review-gated knowledge for long-running Agents without becoming an Agent runtime, hosted RAG
service, remote database, team collaboration service, or general Markdown editor.

The v0.7 compatibility deployment exposes two isolated surfaces:

1. the general Knowledge Asset core, exposed only through optional read-only `knowledge_support`;
2. the version-aware Chinese Legal Pack, exposed only through optional read-only `law_support`.

They do not share canonical storage, Authority decisions, cache, ranking, receipts, lifecycle, or
MCP process. Those are trust and deployment boundaries within one governed Knowledge OS, not
separate product definitions or permission to duplicate identity, graph, versioning, or retrieval
semantics. Neither plugin auto-activates. Case-private documents, facts, chats, and identifiers
belong outside both surfaces.

## Canonical state and trust

A Vault's canonical state is:

- owner-only SQLite;
- immutable, content-addressed source bytes and fragments;
- append-only audit and Identity snapshots.

Markdown, Obsidian pages, JSON Canvas, summaries, tags, graphs, embeddings, model output, search
ranks, and confidence values are derived data. They cannot replace evidence, activate knowledge,
establish authority, prove applicability, or grant permission to execute text.

Source text is always untrusted input. Only an active, human-verified constraint/rule/procedure may
carry `directive_mode=reviewed_instruction`, and it remains subordinate to host, repository,
developer, and current-user instructions.

## Knowledge Identity v2

The v2 schema separates identities that v0.6 previously coupled:

| Identity | Basis | Mutable? |
| --- | --- | --- |
| Collection | Vault + canonical collection name | no |
| Source Identity | Collection + normalized logical path | stable across content revisions |
| Source Revision | Source Identity + exact bytes/media/origin commitment | no |
| Compilation | Source Revision + adapter/config/Source IR/fragment inventory | no |
| Proposal Set | Compilation + extractor/model/prompt + ordered proposals | no |
| Knowledge Key | stable semantic lineage key | stable |
| Knowledge Revision | statement + ordered multi-source refs + scopes/applicability | no |
| Governance Revision | trust/sensitivity/review/lifecycle/activation/export policy | append-only |

The canonical contract is [`knowledge-identity.v2.schema.json`](../contracts/knowledge-identity.v2.schema.json).
Legacy IDs remain compatibility bindings rather than canonical identity.

### Many-to-many evidence

A Knowledge Revision may cite multiple fragments across multiple Source Revisions. A fragment may
support multiple Knowledge Revisions. Exact ref order, `source_revision_id`, `fragment_revision_id`,
locator, quote hash, and logical Source IR node keys are committed.

Compilation can produce `new`, `modified`, `split`, `merged`, `deleted`, or `ambiguous` lineage.
Mapping evidence is retained; a score alone never resolves ambiguity. Cross-key split, merged, and
ambiguous mappings require an explicit source-bound human review. The same immutable mapping is
indexed under every involved Knowledge Key, creates or activates no Asset, and never carries
approval. A source update creates a pending successor and leaves the prior reviewed source active
until atomic review succeeds.

### Temporal relations

Reviewed relations bind stable Knowledge Keys and concrete endpoint revisions. Each immutable
relation revision may contain:

- event time;
- valid-from / valid-to;
- observed time;
- reviewed time;
- ingest time;
- exact evidence refs;
- active/superseded/revoked/ambiguous status.

`current`, `past`, and exact `as-of` views never claim that temporal matching proves factual or
legal applicability. Endpoint forgetting/revocation removes the relation from the current view but
does not erase history.

Source replacement runs a separate relation carry-forward workflow. Exact `unchanged` endpoint
lineage and exactly mapped active evidence may create an inactive `carry_forward` candidate;
`modified`, `renamed`, or `moved` endpoints create `full_review` candidates. Split, merge,
ambiguous, deleted, missing-lineage, or ambiguous-evidence cases are blocked. Every candidate is a
new immutable relation revision with `status=proposed`, and only a fresh explicit human decision
can append the active successor. Golden review and the Workbench expose this queue without making
operators copy internal IDs.

## Source Adapter and Source IR

Every ingestion path first creates bounded Source IR. The adapter contract preserves source order,
boundaries, locators, hashes, hierarchy, and parse configuration.

Current adapters cover:

- Markdown/TXT headings and blocks;
- PDF pages and layout-derived blocks;
- DOCX headings, paragraphs, lists, tables, footnotes/endnotes where present, after a bounded whole-
  archive safety inventory;
- PPTX relationship-defined slide, object, table, and speaker-note order;
- XLSX sheets, rows, cells, formulas, and values;
- EPUB relationship-validated spine documents;
- Python AST symbols/imports/references, plus pinned compiler-grade Tree-sitter grammars for
  JavaScript/JSX, TypeScript/TSX, Java, Go, and Rust; syntax recovery and bounded lexical fallback
  are explicit quality flags;
- JSON/JSONL/YAML/TOML paths;
- HTML headings, paragraphs, lists, code, tables, and captions;
- CSV/TSV rows and cells;
- exact-pinned SQLGlot AST statements, CTEs, tables, columns, and line spans, with an explicit
  bounded lexical fallback after a parser limit or closed parse failure;
- conversations, tool executions, and structured records.

OOXML and EPUB validate the complete ZIP inventory and package relationships before selected XML
is read. XML byte, node, and depth budgets are closed. XLSX additionally validates worksheet order,
unique bounded coordinates, shared-string indices, cell types, formulas, and merged ranges. These
checks are part of `deeplaw-source-adapters/4` compilation identity.

Two explicit operator connectors feed those same closed adapters through immutable Source
Snapshots:

- HTTPS performs a one-shot, direct TLS fetch only after `--confirm-network`. URL preflight is
  network-free. The URL has no credentials/query/fragment, uses public DNS and port 443, and is
  revalidated after each of at most five redirects. DNS answers must all be globally routable; the
  chosen address is pinned while the certificate is checked against the hostname. Encoded control
  paths, response compression, ambiguous lengths, unsupported type/suffix conflicts, empty bodies,
  and bodies over 64 MiB fail closed. An optional expected SHA-256 binds publisher intent; the
  captured bytes always receive their own SHA-256 and `untrusted` governance.
- local exact-Git reads an existing repository at one full 40- or 64-hex commit object ID. It uses
  closed plumbing argv, bounded output/time, disabled replacement objects, prompts, optional locks,
  global/system config, and lazy fetching. It reads regular blobs with `ls-tree`/`cat-file`, verifies
  each Git object digest, and performs no clone, checkout, hook, filter, or network operation.

Snapshots and manifests are owner-only operator state under the selected Vault. Their closed
record binds connector, requested/resolved locator, canonical origin, collection/logical path,
content size/hash, and Vault identity. Snapshot identity and bytes are reverified when a resumable
v2 ingest job runs. The local Git path remains private metadata and never enters canonical Source
Identity or MCP output. Connectors are one-shot—not pollers or `sync --watch` registrations—and
cannot activate knowledge without the ordinary human review transition.

The Source IR contract is [`source-ir.v1.schema.json`](../contracts/source-ir.v1.schema.json).
Structure get/list/search/trace operates on Source IR; it does not require an LLM Wiki.

Adapters fail closed on bounded-size, malformed archive/XML, unsafe member/path, decompression,
symlink, and parser errors. An adapter result is extraction input—not approval.

## Many-to-Many Compiler

Compiler modes are explicit:

| Mode | Status | Boundary |
| --- | --- | --- |
| `off` | Supported | reference proposals only |
| `deterministic-v2` | Supported | replayable local typed extraction |
| `deterministic-v1` | compatibility | retained for v0.6 replay |
| `local-model-v1` | Operator-only | exact executable/model/prompt manifest |
| `external-model-explicit` | Operator-only | exact manifest plus per-run disclosure confirmation |

No compiler may write active knowledge. Output is always `proposed` or `quarantined`; model labels,
confidence, similarity, and extraction success do not satisfy human review. Imported text cannot
be interpreted as instructions for the compiler process.

The closed Typed Compiler scorer reports precision, recall, F1, hallucinated and unsupported claim
rates, exact source-span correctness, duplicates, review acceptance, and cross-document synthesis
correctness from explicit evaluator labels. Its checked synthetic fixture validates metric
semantics only and remains `claim_eligible=false`; a frozen reviewed corpus is still required for a
compiler-quality claim.

## Governance, review, and Proposal Inbox

Activation is a local operator decision bound to exact content and proposal membership. Review
Receipts record reviewer, policy, reason, decisions, hashes, and audit anchor. The current local
receipt is intentionally unsigned.

Agent-generated or external artifacts enter a physically isolated Inbox. A promoted
`.dlproposal` becomes its own untrusted Source Revision and generates a source-bound Identity v2
quarantine. `.dlfeedback`, `.dlrun`, and `.dleval` remain bounded operator inputs. Inbox APIs never
appear on MCP.

Manual source-free proposals are supported for deliberate local synthesis but remain explicitly
`legacy-unbound`; they are not represented as source-authoritative Identity v2 knowledge.

Identity v2 relation revisions require an exact fragment from an active reviewed Source Revision.
Their governance sensitivity is the maximum of both endpoints and the evidence source. Source-free
v0.6 compatibility edges remain locally inspectable, but the Retrieval Fabric, Golden `recall`,
and MCP Context path do not use them as graph evidence. Restricted, superseded, removed, or
unreviewed relation evidence is rejected again at retrieval time; history remains available in the
explicit past view.

Lifecycle is explicit:

```text
proposed / quarantined → active → superseded / revoked / expired / deleted
```

Generated confidence is never approval. Restricted knowledge never becomes Agent-readable.

## Evidence-Governed Retrieval Fabric

Retrieval begins with a canonical Query Plan containing normalized query, intent, Knowledge Duties,
channels, channel budgets, filters, temporal scope, ranking profile, reranker profile, tokenizer
profile, and implementation revision. `query_plan_id` commits to that complete plan.

Candidate channels are:

- exact Asset URI/ID, Knowledge Key, semantic key, and explicit phrase;
- fielded lexical BM25 with CJK/mixed-language query normalization;
- Source Tree candidates;
- reviewed relation graph neighbors with a bounded two-hop traversal;
- current or as-of temporal candidates;
- bounded structured-feedback signals;
- an explicitly supplied, exact-model Discovery Index;
- an explicitly supplied pinned local reranker.

Channels only propose candidates. Central Admission then enforces active/reviewed lifecycle,
expiry, sensitivity, source integrity, source refs, as-of governance, scope, case-data boundary, and
other policy. Selection applies versioned RRF weights, source diversity, type/Duty priority, token
and character budgets. Ranks and fusion values are not probabilities or authority.

Distinctive identifiers are searched before source-wide common terms. Hybrid Source Tree retrieval
uses bounded lexical/exact seeds where available, avoiding a full node scan and preventing one large
source from swamping results. If ordinary lexical retrieval has no candidate, a bounded FTS-prefix
candidate set may be post-filtered by exact one-edit ASCII distance (including adjacent
transposition); broader fuzzy search is not implied. Graph traversal caps seeds, frontier, hops, and
total candidates, and every edge must independently pass reviewed source-evidence admission.

The optional local reranker manifest pins executable, closed argv, model identity/revision, exact
resource hashes, candidate/input/output bounds, and timeout. It must output an exact permutation of
the supplied candidates and cannot introduce IDs or numeric confidence. DeepLaw supplies a minimal
process environment but does not claim it is an OS network sandbox.

## Knowledge Duties and Capsule

Query intent selects explicit duties such as constraints, applicability, current decisions,
procedures, definitions, lessons, recent changes, exceptions, conflicts, open questions,
counterevidence, and missing evidence. Missing duties remain gaps rather than being filled from
model memory or web search.

The Context Compiler packages admitted candidates into a Knowledge Capsule with hard limits for:

- selected items;
- excerpt characters;
- serialized payload;
- source refs;
- exact or labeled-estimated tokens.

Every source-bound selected item retains at least one compact exact reference. Capsule verification
replays audit/state/source/hash/plan bindings and rejects tampering, stale lifecycle, missing source
files, changed query plans, invalid refs, or budget violations.

## Capsule-bound Run Record and feedback

A task Run Record can only bind a real, currently verified Capsule. It commits to Vault revision,
audit head, Capsule identity, Asset inventory, source inventory, host identity, and explicit outcome.
DeepLaw never infers task success from command completion.

Structured feedback distinguishes helpful, irrelevant, harmful, stale, missing knowledge, missing
source, incorrect relation, and budget failure. It binds the Run/Capsule inventory and may generate
review-gated lesson proposals or source-free regression cases. Profile training/evaluation also
requires Run/Capsule/Feedback-bound data; activation runs full regression and can roll back.

## Human projection and Workbench

The curses Operator Workbench uses the same service layer as CLI. It includes Source List/Tree/Diff,
side-by-side review, approve/reject/edit/split/merge, visible-row cross-key Lineage mapping,
search/recall, Explain Trace, lineage, relations, current/history, Capsule, feedback, health, and
benchmark status. Approve/reject batches are one transaction and cannot leave a partial decision;
quarantined approval requires a separate risk confirmation. It opens no socket and sends no
telemetry.

Rich projection emits Markdown and JSON Canvas for sources, concepts, decisions, constraints,
procedures, experiences, questions, relations, history, Capsules, and feedback. Projection is a
deterministic human view, never a second database. Reverse edits produce a diff and quarantine;
they cannot mutate active Assets or inherit approval.

## Skill Factory

Skill Factory derives bounded, source-bound read-only skills from active Knowledge. A bundle pins:

- exact Knowledge Keys and revisions;
- source hashes and compact refs;
- scope and token budgets;
- `SKILL.md`, knowledge payload, and test inventory hashes;
- minimum regression fixtures.

External bundles default to quarantine. A skill contains no ingest, review, approve, delete,
feedback-write, or administration command.

## Reliability and local confidentiality

Implemented operations include resumable ingest, retry, cancel, crash recovery, source watch,
atomic update, snapshot, restore, GC, orphan detection, derived-index rebuild, migration, rollback,
corruption doctor, and backup validation.

POSIX Vaults use owner-only modes and reject symlink roots/protected files. Windows code hardens and
verifies native ACLs, owner SID, Users, Everyone, inherited access, reparse points, junctions,
sources, models, and indexes. Windows-only CI tests are present, but a macOS run is not Windows
evidence; final candidate status remains `External verification pending` until the workflow runs.

DeepLaw does not invent an encryption protocol. Operators should enable OS full-disk encryption.
MCP read-only does not neutralize an arbitrary same-user Shell granted by the host; host tool policy
or a separate OS identity must enforce that boundary.

## Portable packages and Discovery

`.dlk` v1 provides content integrity only. Publisher identity, signatures, trust rotation,
revocation, and transparency are not implemented; every import loses source trust and enters
quarantine.

Discovery is an explicit removable sidecar. It binds the exact model profile/revision/files, Vault
revision/audit head, active Asset projections, and index bytes. It excludes restricted, inactive,
expired, legal-release, and case material. It remains outside both MCP servers and default Context
until a frozen held-out gate passes.

## Agent interface

`knowledge_support` exposes bounded read-only inspection, search, exact get, and Context operations.
Provider-visible search returns at most five evidence cards; exact full text requires an exact ID.
There are no MCP tools for corpus writes, memory writes, learning, Inbox promotion, review,
approval, feedback recording, import, delete, migration, or administration.

## Validated and pending evidence

The repository includes contract/lifecycle/security tests, Golden CLI acceptance, Workbench smoke,
fresh-wheel verification, dependency/OpenVEX audits, SBOM/license/package inventory tooling,
reproducible build checks, and a 100k/1m scale runner. Development diagnostics remain
`claim_eligible=false`.

Actual 100k and one-million construction diagnostics are checked in and remain claim-ineligible
because they bind a dirty worktree and synthetic exact-token corpus. The formal v0.7 software
release completed its release gates, but neither that release nor v0.9 can reuse these diagnostics
as competitive evidence. Every named official baseline, preregistered statistical gate, two secret
held-out suites, and two genuine independent evaluator signatures remain external requirements.
The development team cannot manufacture external independence.

## Deliberate non-goals

- multi-tenant SaaS, team RBAC, or remote canonical storage;
- automatic Agent memory activation;
- treating generated Wiki/graph/embedding/rank as truth;
- case-project storage;
- unrestricted MCP output or graph traversal;
- a general Markdown editor;
- implicit URL/web/model fallback;
- cross-system leadership claims without frozen external evidence.
