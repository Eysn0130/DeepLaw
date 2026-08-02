# DeepLaw Autonomous Knowledge OS

Status: **Current v0.12.0 contract**, 2026-07-30. This document defines the released Autonomous
Knowledge Core and 0.9 Living Wiki / Knowledge Intelligence implementation. Historical v0.7
proposal/review documents remain source-governance and migration evidence, not the default policy
for new Agent-derived knowledge.

## 1. Product boundary

DeepLaw is a local, single-user, owner-controlled knowledge layer for Agent runtimes. It does not
own models, conversations, generic tool execution, legal adjudication, or a remote control plane.
It compiles durable local knowledge into bounded, auditable context for an explicitly activated
host.

The implementation has two semantic knowledge planes:

1. **Immutable evidence**: exact official or user-provided bytes, content-addressed revisions,
   fragments, locators, parser provenance, and lifecycle records.
2. **Agent-derived knowledge**: claims, concepts, entities, events, decisions, procedures,
   experiences, preferences, syntheses, comparisons, memory, relations, Wiki navigation, and
   versioned Skill knowledge.

SQLite and derived indexes support those planes. They do not create a third authority class.

## 2. Current storage contract

```text
immutable object repository
  + Markdown-native knowledge workspace
  + SQLite trusted identity/event Ledger
  + rebuildable retrieval and visualization indexes
```

### Immutable objects

`.deeplaw/objects/sha256/<prefix>/<digest>` stores exact bytes. `content_objects_v3` binds each
digest to its first-observed media metadata and byte size; `content_object_roles_v3` independently
binds every permitted semantic role. Identical bytes may therefore be both evidence and a
Knowledge Revision without collapsing their separate identities, governance, or provenance.
Existing v0.7 source bytes are copied into this repository during migration and checked against
their original Ledger hash. Successful legacy-compatible CLI writes close their source transaction
before synchronizing new exact bytes and evidence bindings into the autonomous core. `verify` and
the default Vault `doctor` compare the complete source inventory with those bindings, so a caller
that bypasses the shared CLI/domain path cannot leave an undetected half-migrated evidence plane;
opening the autonomous store for an authorized write performs the idempotent repair.

An ingested revision is never overwritten in place. A new byte sequence receives a new digest and
revision. `forget` removes recall, workspace, and derived-index eligibility while retaining
revision governance and audit history. Owner-confirmed content GC may erase CAS bytes only when
every referring Agent Knowledge lineage is forgotten or revoked (or explicitly opted-in expired),
never when the same bytes have an evidence role. Unregistered CAS bytes remain protected through a
bounded grace window so GC cannot race a coordinator between object publication and Ledger commit.
GC first commits a recoverable tombstone/event, then removes bytes; startup recovery completes an
interrupted purge. Historical reads report
`content_purged` instead of inventing the body. User-source deletion and user-private Legal Pack
deletion remain separate owner operations with their own policies.

### Markdown Knowledge Objects

The canonical open content of Agent-derived knowledge is UTF-8 Markdown with constrained YAML
frontmatter. A file includes stable `deeplaw_id`, revision ID, kind, lifecycle, epistemic state,
origin, authority, verification, scope, sensitivity, writer, source references, generation/run
identity, valid/transaction time, tags, parent, supersedes, TTL, and type-specific metadata.

YAML aliases and duplicate keys are rejected. Unknown frontmatter fields fail the contract. A
filename, title, directory, alias, or Wikilink is never an identity.

### Trusted Ledger

The additive STRICT tables in `.deeplaw/ledger.sqlite3` record (a legacy root `vault.sqlite3` is
promoted atomically during migration and remains discoverable only for v0.7 compatibility):

- autonomous schema identity and metadata;
- immutable object records and evidence bindings;
- Knowledge Object identities and immutable revisions;
- versioned, bitemporal relation revisions;
- sink grants, idempotency, usage, immutable Run Records, capture batches, feedback, duplicate and
  identity resolutions, consolidation records, aliases, leases, and content tombstones;
- pending materialization, explicit workspace conflicts, and rebuild work;
- an independent append-only hash-chained autonomous event stream.

Run Records retain writer, host, model, scope, sensitivity, status and time, plus task and optional
prompt/input, output, tool-result, note and artifact digests. Raw prompts, conversations and hidden
reasoning are not copied into durable memory merely to satisfy provenance; their immutable digest
binds an explicitly retained owner artifact when one exists.

A complete Knowledge Revision is the pair of an exact Markdown object hash and its Ledger record.
`verify` fails on event-chain damage, a missing or orphan domain event, invalid genesis/replay,
SQLite integrity failure, missing/tampered CAS bytes, or an active workspace/Revision hash
mismatch.

### Rebuildable layers

FTS, local dense vectors, graph adjacency, deterministic weighted communities, Living Wiki
navigation, semantic-lint/gap reports, JSON Canvas, and query caches are disposable. Their manifest binds the
input audit head, generator/version, configuration, revision-set counts and digests, generated
files, and hashes. Generated Wiki/lint/community views have explicit item, member, file, and byte
limits; a large Vault cannot grow one derived file or manifest without bound.

The current default implementation adds a deterministic offline multilingual hash-dense index and
evidence-duty reranker to lexical and graph navigation. Their manifests bind model identity,
revision inventory, both audit heads, exact index bytes, dimensions/quantization and
`network_policy=offline`; stale or damaged indexes are rejected and canonical lexical retrieval
remains available. Source Tree and code-symbol channels remain in the source-derived compatibility
plane. Model-generated entity extraction or GraphRAG prose summaries are not silently enabled:
Concept/Entity/Event creation uses the closed Sink contracts, and community pages remain derived
navigation views rather than knowledge or authority.

## 3. Autonomous mutation lifecycle

The `knowledge_sink` domain path applies this sequence:

1. **Capture** only explicit durable, reusable content through a Run-bound closed batch; local
   paths, secret-like content, unconfirmed case data, raw conversation and hidden chain-of-thought
   are rejected rather than treated as persistence instructions.
2. **Classify** kind, exact scope, maximum sensitivity, epistemic state, temporal interval, TTL,
   source binding, and generation activity. Persisted `working` memory must have an explicit TTL;
   a Claim must name at least one Source reference or an
   immutable Run Record; an invalid claimed binding is quarantined, while an omitted binding is a
   closed-contract rejection.
3. **Admit the capability** by verifying an owner-only token, writer identity, allowed operations,
   evaluator identities, request bytes, rate, and object capacity. A normal Agent grant defaults
   to `agent_self_report`; `user` and `external_check` labels require a separate owner-granted
   evaluator capability.
4. **Reconcile** idempotency and compare-and-swap. Exact same-scope semantic duplicates collapse to
   the existing stable ID with an immutable resolution/event and idempotent receipt. Semantic
   candidates may produce aliases or explicit same-as/merge/split decisions; high-precision
   contradictions remain independent contested objects/relations instead of being silently merged.
   Existing Knowledge Object and relation updates require their exact parent revision. A direct
   mutation refuses to overwrite a workspace body that has not first been reconciled.
5. **Gate risk**. Unknown provenance, origin/authority elevation, direct-user-statement
   misclassification, stored prompt injection, or governance metadata edits are quarantined.
6. **Commit** the CAS Markdown bytes, immutable Ledger revision, event, usage, idempotent response,
   pending materialization, and rebuild request in a recoverable transaction.
7. **Materialize** the current Markdown file. A crash leaves a pending record that `recover`
   deterministically replays. Every writable store startup performs this bounded recovery before
   accepting another mutation; malformed staging records fail closed, and intents with no atomic
   Ledger commit are discarded without treating their orphan CAS bytes as knowledge.
8. **Connect and rebuild** through the durable derived-work queue. Canonical commits do not wait for
   FTS, dense vectors, graph projection, lint/gaps, Living Wiki, weighted communities, or Canvas.
   The explicit foreground Watcher drains queued work after reconciliation, and `rebuild` is the
   deterministic operator path. Failure leaves work queued; current reads reject stale manifests
   and use bounded canonical fallback. Wikilinks compile through stable identity resolution; an
   ambiguous link remains a reported gap.
9. **Learn** through a distinct feedback record. `agent_self_report` is explicitly weaker than a
   `user` or `external_check` outcome and cannot establish task success by itself.
10. **Decay, consolidate, or forget** through TTL expiry, a crash-safe consolidation saga, or an
    explicit immutable lifecycle revision. Consolidation preflights its relation sub-capability and
    verifies every evidence-bound lineage edge before archiving an input. Current workspace and
    indexes stop exposing inactive content; history and audit identities remain. An owner may
    separately purge eligible bytes without deleting governance history.

“Decay” is therefore an explicit eligibility/lifecycle transition driven by bounded TTL or owner
policy, not a hidden confidence score that silently changes Authority. Usage feedback may influence
future selection or a later revision, but it cannot mutate provenance, verification or permissions.

CAS publication, staging recovery, Markdown materialization/reconciliation and content GC share one
Ledger-backed `canonical-mutation` lease. Nested calls in the same coordinator are reentrant; a
second writable process fails explicitly instead of racing file state. SQLite transactions continue
to serialize Ledger-only mutations, and derived rebuilds use a separate disposable-layer lease.

Agent-derived revisions become `active` without per-item human review when all policy checks pass.
This means “allowed as memory,” not “factually verified,” “official,” “user said this,” or “allowed
to execute.”

## 4. Authority and provenance invariants

The following dimensions never collapse into one score:

| Dimension | Examples | Who may change it |
| --- | --- | --- |
| origin | `official`, `user_source`, `agent_derived`, `external_import` | evidence/revision governance |
| verification | signed official, user-provided, source-bound, run-bound, unverified | verifiable binding or owner review |
| lifecycle | active, superseded, revoked, expired, forgotten, quarantined | lifecycle service under policy |
| scope/sensitivity | personal/project/domain; public/internal/private/restricted | explicit owner grant/policy |
| mutability/writer scope | immutable revision bytes; `revision_only`; exact granted scope | revision coordinator |
| activation policy | `deeplaw.autonomous-activation/v1` | repository/owner policy, never content |
| provenance | source/fragment, Knowledge Revision, run/activity, writer/model/tool | immutable write record |
| temporal | valid-from/to and recorded-at | revision/event only |

Every autonomous Knowledge Object is fixed to `origin=agent_derived`,
`authority=agent_derived`, `legal_authority=false`, `mutability=revision_only`, its granted
`writer_scope`, and `activation_policy=deeplaw.autonomous-activation/v1`. These values are present
in constrained frontmatter and the Ledger-bound metadata/event digest. A workspace edit cannot
change them. Ranking, link count, community membership, feedback, model confidence, or repeated
retrieval cannot change these fields. Source-free knowledge defaults to `tentative` and cannot
self-declare the `supported` epistemic state.

A preference records `preference_basis=direct_user_statement` or `agent_inference`. A direct-user
label without a source/run binding is quarantined. External source text never receives instruction
semantics merely because it resembles a command.

Legacy source aliases are resolved to their immutable `source_revision_id` before a new Knowledge
Revision or relation is committed. The current legacy source/Inbox lifecycle remains an admission
input: a superseded, removed, or rejected source can still verify historical lineage but cannot
support a new relation or enter Agent recall. This deliberately favors owner deletion and
fail-closed provenance over reconstructing an earlier source-admission policy from incomplete
legacy lifecycle history.

Ordinary sink grants cannot move an existing lineage to another scope or lower its sensitivity.
Knowledge and relation provenance must itself be visible in the same scope and at or below the
containing revision's sensitivity. Relations inherit the maximum sensitivity of both endpoints and
their bound evidence, so a public graph query cannot reveal a private supporting edge. Every new
relation requires at least one bound Source, Run artifact, or Knowledge Revision evidence reference;
historical source-free relation rows remain auditable but are never admitted to current graph or
recall. A Markdown relation hint that cannot be compiled under the active capability or evidence
policy remains an explicit Semantic Lint / Gap finding; it is never treated as a canonical edge.
If a bound Inbox artifact or source later becomes rejected/inactive, immutable revision history
still verifies, but that Knowledge Object or relation is no longer admitted to recall, graph, MCP
exact reads, or rebuilt FTS/Wiki views; semantic Lint reports the inactive provenance.

## 5. Open workspace reconciliation

`deeplaw knowledge autonomy reconcile` scans only the bounded `knowledge`, `memory`, and `skills`
workspaces. It verifies the grant and each file contract.
`deeplaw knowledge autonomy watch` is an explicitly started foreground polling loop over that same
method; it adds no second mutation implementation or background daemon. The interval is bounded to
0.25–3600 seconds, each cycle rechecks the grant, and `--max-cycles` provides a deterministic
automation/test boundary.

- Rename/move with unchanged bytes updates only the stable workspace location and records an event.
- A content edit based on the current revision becomes a new immutable revision.
- A direct CLI/Sink revision preserves an already reconciled rename/move. If the current workspace
  bytes differ from their Ledger-bound revision, the attempted overwrite fails closed and the
  divergent file is preserved as a conflict for explicit reconciliation.
- A stale base, duplicate stable ID, invalid ID, unsupported kind, or unsafe path is copied to
  `.deeplaw/staging/conflicts`, entered in the Ledger, and removed from the canonical path.
- An attempted frontmatter authority, source, scope, or sensitivity change becomes quarantined and
  the prior current revision is restored.
- Deleting a file in an editor is not implicit forgetting; the current revision is restored. Use
  the explicit `forget` operation for lifecycle removal.

This is intentionally not last-writer-wins.

## 6. Query and Capsule contract

The query pipeline keeps these stages distinct:

```text
Discovery != Admission != Selection != Authority != Adjudication
```

`recall` discovers exact IDs, current/historical lexical candidates, the bound local dense index,
current or transaction-time canonical graph neighbors, and reranked candidates. Scope,
sensitivity, lifecycle, valid time,
kind, and required-tag boundaries are applied before each bounded lexical/dense/graph candidate
cut whenever the canonical channel can express them; the unified admission pass then revalidates
every candidate and provenance before reranking. It admits by lifecycle, provenance, exact
scope, maximum sensitivity, kind/required tag, TTL, valid time, and transaction time. Selection
applies independent item, character, token, source, graph-hop, and provider-payload budgets.
Unauthorized scope or sensitivity candidates are omitted
without identifiers or counts; admitted candidates can still expose bounded rejection reasons.
The v3 `kinds` filter is the explicit union of legacy Asset kinds and autonomous Knowledge kinds;
each partition receives only compatible values, and an all-plane query assigns the full bounded
budget to the sole compatible partition instead of probing the other namespace. The retained
`memory_tiers` filter belongs only to the legacy source-derived model; using it in an all-plane
query likewise excludes the autonomous partition, while an autonomous-only request fails closed.
Historical lexical recall reads the immutable revision content that existed at `as_of`, not the
current FTS text. A current `forgotten` or `revoked` tombstone cannot be bypassed with `as_of`;
owner audit remains available through lineage history. The result contains channels, selection
reasons, rejected candidates, gaps, contradictions, a replayable Query Plan, its hash, the audit
head, admitted-candidate lifecycle/provenance state digest, rebuild-manifest digest, and a
statement that ranking did not change authority. Dense and reranker scores are discovery/ordering
signals only; they never appear as confidence, verification, permission, or legal authority.
For current-time reads, the plan also states whether the derived lexical projection is bound to
both current audit heads and has no pending rebuild. When it is stale or unavailable, retrieval
uses a scope-filtered canonical Markdown scan capped at 500 current objects, records the fallback
channel, and emits an explicit gap if that scan truncates; stale FTS is never silently presented as
complete.
The default Capsule path uses the same purpose-aware Query Plan v5 service as CLI `query`, the
Python API, and MCP. The plan binds both the autonomous audit head and the legacy evidence/Inbox
audit head, plus the
admitted-candidate state and derived-manifest digests, so lifecycle changes outside the autonomous
event stream cannot masquerade as the same replay input.
It also binds autonomous kind/tag filters, retrieval mode, token/source/hop budgets, dense model,
reranker profile and dense-manifest digest. The source-derived partition emits its own hashed compact
plan binding query, compatible kind/memory-tier filters, scope, sensitivity, budget, Vault revision,
legacy audit head, and historical intent when that compatibility partition is explicitly selected.
Target identity admission applies before a raw-evidence fallback, so a stale or withdrawn named
policy cannot be replaced by another policy merely because it is the nearest remaining hit.

The retained v0.7 source-derived partition currently has no transaction-time history contract.
Consequently an `as_of` query does not silently substitute its current assets: that partition is
empty and the Query Plan records an explicit historical-source gap. Exact historical `asset_id`
reads fail closed. This prevents current legacy state from being mislabeled as historical evidence.

Knowledge Capsule v2 partitions:

- official evidence (empty in `knowledge_support`; use `law_support`);
- user-private legal evidence (empty in `knowledge_support`; use `law_support`);
- legacy/source-derived knowledge;
- Agent-derived knowledge;
- Agent memory;
- contradictions, limitations, gaps, and receipts.

The provider-visible response has a hard 64 KiB limit. Source metadata, tags, bodies, graph edges,
and histories are bounded independently. `restricted` content is never available to MCP hosts.
Before either v1 or v3 `knowledge_support` response leaves the process, a recursive projection
gate fails closed on local absolute paths or recognized secret material; it never reports the
matched value in its error. Unsafe invisible/bidirectional Unicode is rejected at the same gate,
and MCP exception text crosses the same projection rather than reflecting sensitive failure
details.

## 7. MCP and capability separation

### `knowledge_support`

- separate local stdio process;
- one leaf named `knowledge_support`;
- read-only MCP annotations;
- v3 contract after autonomous migration, frozen v2 contract for the initial autonomous seam, and
  v1 compatibility contract for untouched v0.7 Vaults;
- search/recall, get, context, verify, inspect, lineage, graph, Wiki lookup, identity lookup, and
  bounded gap discovery;
- an `explain` operation for Query Plan, admission/selection receipts, gaps, and budgets;
- no remember, relation mutation, forget, grant, import, legal-source write, or arbitrary path.

### `knowledge_sink`

- independently started local stdio process;
- one leaf named `knowledge_sink`;
- explicit write/destructive annotations;
- closed input/output JSON Schema;
- concrete owner-created grant ID, owner-only token, writer identity, exact scope, maximum
  sensitivity, operation allowlist, request/rate/capacity limits, idempotency, and audit;
- no Legal Pack or source mutation, authority elevation, permission grant, audit deletion,
  arbitrary filesystem path, export, signing, or case data.

Creating the autonomous core does not create a sink grant or Run Record. The default Knowledge OS plugin only
registers `knowledge_support`. A newly enabled sink grant defaults to `remember` only; every other
operation must be named explicitly. `remember` accepts ordinary object kinds but cannot smuggle in
`concept`, `synthesis`, or `skill`; those kinds require `upsert_concept`, `save_synthesis`, or
`save_skill` respectively in both the request and grant. An omitted MCP scope resolves to the
selected Vault/grant scope rather than silently assuming `project`.

### `law_support`

The legal query server remains a separate read-only process and store. Official catalog build,
signature, install/update, private add/delete, and active-pointer changes remain CLI-only owner or
maintainer operations. Its v3 contract adds `federated_context`, which independently searches the
official and user-private releases and, only when explicitly enabled, tag-admitted Agent legal
interpretations. Each partition retains origin, authority, version/receipt and `legal_authority`;
an empty/unavailable official partition never relabels another source as official.

## 8. Skill knowledge

`save_skill` stores a Skill as a versioned Agent-derived Knowledge Object. `skill-draft` is the
deterministic Skill Factory seam: it reads admitted Procedure/Experience/Decision/Synthesis
revisions, accepts only explicit `instruction => completion criterion` (or `::`) steps, abstains on
vague/non-checkable procedures, and commits the generated draft through the same coordinator. It
does not execute the Skill or grant capabilities. The closed Skill manifest
requires purpose, applicability and exclusions, invocation mode, input/output contracts,
capabilities, resource limits, ordered steps with completion criteria, success/failure conditions,
source and evaluation revisions, license, host compatibility, verification commands, limitations,
supersession, deprecation, and lifecycle.

Skill lifecycle is `draft`, `experimental`, `promoted`, `deprecated`, or `revoked`. Promotion
requires a helpful `user` or `external_check` evaluation run bound to the same Skill lineage; a
self-report is insufficient, and the evaluator label must be permitted by the grant that records
it. A model-invoked Skill cannot declare owner-only signing,
publishing, private export, irreversible deletion, or permission-grant capabilities. Stored Skill
text never grants tools by itself; the host and owner policy remain authoritative.

## 9. Migration, recovery, snapshot, and rollback

### New Vault

```bash
deeplaw knowledge init --vault ./vault --name project --scope project
```

This creates the retained v0.7 compatibility schema plus the v0.9 autonomous core. No mutation grant is
enabled. `--legacy-review-core` exists only for compatibility testing and staged migration.

### Existing Vault

```bash
deeplaw knowledge autonomy migrate --vault ./vault --backup ./pre-autonomy-backup
deeplaw knowledge autonomy verify --vault ./vault
deeplaw knowledge autonomy explain --vault ./vault --query "authority boundary"
deeplaw knowledge autonomy graph --vault ./vault --limit 20
deeplaw knowledge autonomy conflicts --vault ./vault

deeplaw knowledge autonomy watch \
  --vault ./vault --grant-id grant_REPLACE_WITH_RETURNED_ID \
  --confirm-no-case-data --interval 2
deeplaw knowledge sink status --vault ./vault --grant-id grant_REPLACE_WITH_RETURNED_ID

# Rebuild FTS, local dense vectors, graph, Wiki, communities and Canvas.
deeplaw knowledge autonomy rebuild --vault ./vault

# Dry-run owner content erasure first; only the second command removes eligible bytes.
deeplaw knowledge autonomy gc --vault ./vault
deeplaw knowledge autonomy gc --vault ./vault --no-dry-run --confirm \
  --reason "owner retention policy"

# Compile explicit Procedure steps into a governed draft Skill revision.
deeplaw knowledge autonomy skill-draft \
  --vault ./vault --grant-id grant_REPLACE_WITH_RETURNED_ID --request ./skill-draft.json
```

Migration first creates and verifies a complete v0.7 rollback point, then installs v3 STRICT tables,
workspace directories, CAS bindings, event genesis, recovery state, and manifest. It is additive;
legacy Source IR and reviewed assets remain readable in a separate source-derived partition.

Rollback is fail-closed and retains the replaced Vault beside the restored target:

```bash
deeplaw knowledge autonomy rollback \
  --vault ./vault --backup ./pre-autonomy-backup --confirm
```

Autonomous snapshots include the canonical database, evidence views, Inbox provenance artifacts,
Markdown revisions, CAS, staging/conflicts, and capability state. By default they also preserve
resumable operation/source-snapshot records and retrieval-profile operator state; the existing
`--no-include-operator-state` switch excludes those non-canonical sidecars. Rebuildable
FTS/Wiki/Canvas/cache layers are excluded and
must be regenerated after restore. Snapshot manifests inventory and hash every included file and
bind both legacy and autonomous audit heads. Capability state includes owner-only authentication
material, so snapshots are credentials: keep them owner-only and never commit, publish, or share
them as ordinary backup artifacts.

## 10. Legal Pack invariants retained

The official Legal Pack implementation retains exact-byte Ed25519 catalog verification before
parsing/downloading, public trust roots, key revocation, catalog identity, monotonic sequence,
rollback protection, fail-closed builds, immutable releases, and atomic active-pointer switching.
Network catalogs never use the local unsigned-development bypass.

The user-private legal library remains owner-only and physically independent. A private copy of an
official-looking file does not inherit official identity. Private add/delete cannot alter official
catalog, pointer, cache, ranking, receipt, or release.

DeepLaw delivers evidence and context; it does not decide legal applicability, facts, liability,
or a verdict.

## 11. Verification and test evidence

The implementation is exercised by:

- `tests/test_source_compilation.py`: persisted Source IR packets, closed Plans, multi-batch atomic
  publication, identity ambiguity, revision dependencies/freshness, source successor/withdrawal,
  rich sharded Wiki projection, purpose-aware retrieval, controlled backfill, API/CLI/MCP parity,
  migration/snapshot/rollback, failure recovery, and deterministic fake-Agent E2E;
- `tests/test_editor_bridges.py` and `tests/test_living_wiki_delivery.py`: Editor Context Envelope,
  Obsidian/Tolaria path and persistence boundaries, frozen comparative fixtures, and honest
  real-host `not_executed` reporting;
- `tests/test_autonomous_knowledge.py`: migration/rollback, immediate active revision,
  CAS/Markdown/Ledger binding, authority/injection quarantine, reconcile/conflicts/recovery,
  historical revision semantics, provenance/scope/sensitivity isolation, tamper detection,
  derived-staleness handling, relations/Wiki/lint/Capsule/forget, scope/rate/token/TTL, and safe
  YAML, pre-Top-K governance filters, evidence-required relations, exact identity ambiguity, and
  scope/sensitivity-safe gap discovery;
- `tests/test_knowledge_sink_mcp.py`: closed and separate MCP surfaces, federated read partitions,
  hard budgets, temporal get, scope isolation, explain/lineage/graph/Wiki/Capsule, feedback
  authority, provenance quarantine, exact dedup, externally gated Skill promotion, preference
  governance, snapshot/restore, and real stdio tool discovery;
- `tests/test_knowledge_cli.py`: a real foreground Watcher cycle commits an external Markdown edit
  through the same reconcile/coordinator path and exposes the resulting revision through CLI;
- the existing legal, source, retrieval, package, migration, security, plugin, release, and full
  regression suite.

Required repository checks:

```bash
uv run pytest
uv run ruff check .
git diff --check
```

## 12. Evidence-limited claims

This delivery establishes implementation and regression evidence. It does not establish that
DeepLaw is better than RAG, GraphRAG, PageIndex, Mem0, Cognee, MemOS, or other systems on real tasks.
Such a claim still requires a frozen corpus and candidate set, preregistered baselines, held-out
questions, fixed host model/prompt/permissions/budgets/hardware/network/cost, confidence intervals,
failure samples, and independently reproducible artifacts under
[`EXTERNAL_BENCHMARK_PROTOCOL.md`](EXTERNAL_BENCHMARK_PROTOCOL.md).

No external benchmark artifact, upstream license review, model checkpoint, SBOM exception, or
official release signature was fabricated as part of this implementation.

## 13. Working-tree Living Wiki Compiler extension

The current working tree adds the host-neutral compilation saga, rich projection, purpose-aware
retrieval and controlled query backfill described in
[`LIVING_WIKI_COMPILER.md`](LIVING_WIKI_COMPILER.md). It reuses the same Markdown/CAS/Ledger,
identity, grant, admission, reconciliation and verification primitives; it is not a parallel
knowledge engine.

The package version is `0.12.0`. Current implementation,
compatibility and exact gate status are separated in:

- [`LIVING_WIKI_IMPLEMENTATION_AUDIT_2026-07-30.md`](LIVING_WIKI_IMPLEMENTATION_AUDIT_2026-07-30.md);
- [`LIVING_WIKI_COMPATIBILITY.md`](LIVING_WIKI_COMPATIBILITY.md);
- [`LIVING_WIKI_ACCEPTANCE_REPORT_2026-07-30.md`](LIVING_WIKI_ACCEPTANCE_REPORT_2026-07-30.md).
