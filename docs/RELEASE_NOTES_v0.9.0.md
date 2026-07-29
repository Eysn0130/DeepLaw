# DeepLaw v0.9.0 release notes

DeepLaw v0.9.0 delivers the 0.8 Autonomous Knowledge Core and 0.9 Living Wiki / Knowledge
Intelligence phases of the current project brief. It changes the default long-term knowledge model
from universal per-item human review to policy-gated autonomous Agent knowledge while preserving
immutable evidence, owner authority, source integrity, and explicit write capabilities.

Release decision:

```text
commercial_release_eligible=true
competitive_claim_eligible=false
```

The release flag covers the exact software and supply-chain artifact gates. It does not claim
competitive leadership. Real model tasks on Codex, Claude Code, and OpenCode, actual named-system
baseline runs, evaluator-secret held-outs, confidence intervals, and two independent evaluator
signatures remain external work.

## Highlights

- Canonical Agent knowledge is now an immutable Markdown Revision Object paired with a STRICT
  SQLite identity/event Ledger record. Stable identity survives filename, title, alias, and path
  changes.
- A single recoverable commit coordinator handles CAS bytes, compare-and-swap, Ledger state,
  hash-chained events, idempotency, workspace materialization, and crash recovery.
- The new separately enabled `knowledge_sink` supports bounded Run/capture, typed knowledge and
  memory, evidence-bound temporal relations, feedback, consolidation, lifecycle changes, and Skill
  revisions under owner-created grants.
- Policy-admitted Agent-derived knowledge becomes immediately usable without per-item review, but
  remains `agent_derived`, `legal_authority=false`, and unable to grant tools or promote itself to
  official, user-provided, or human-verified.
- Obsidian/Tolaria-compatible Markdown/YAML/Wikilink editing supports stable rename/move,
  reconciliation, explicit conflict preservation, and a foreground Watcher over the same domain
  service.
- Living Overview, typed Wiki pages, Semantic Lint, scope-safe gap discovery, deterministic
  communities, JSON Canvas, memory consolidation, and a checkable-step Skill draft Factory complete
  the 0.9 knowledge-intelligence loop.
- Current retrieval combines exact, FTS, offline multilingual hash-dense, evidence-duty reranker,
  graph, temporal, memory, Wiki, and retained source-derived Tree/code channels under one
  partitioned Knowledge Capsule.
- `law_support` v3 can compile independently admitted official, user-private, and explicitly
  enabled Agent-interpretation partitions without confusing legal Authority.

## Security and correctness changes

- Scope, sensitivity, lifecycle, valid time, kind, and required tags are applied before bounded
  lexical/dense/temporal/graph candidate cuts and rechecked before reranking. Unauthorized or
  irrelevant high-ranking candidates cannot crowd out admissible knowledge.
- Semantic Lint and gap discovery now enforce the caller's scope and maximum sensitivity across
  objects, relations, and aliases; other partitions cannot leak through IDs or aggregate counts.
- Every new relation requires a valid evidence reference. Historical source-free rows remain
  auditable but are not admitted to current graph, recall, or contradiction challenge.
- Relation hints that cannot become admitted canonical edges remain explicit Semantic Lint / gap
  findings. Memory consolidation verifies every evidence-bound lineage edge before archiving input.
- Identity lookup binds aliases to the current revision, filters governance before its bounded
  scan, preserves exact ambiguity even with a one-item return limit, and reports truncation.
- Graph, contradiction, identity, and Lint scans have explicit resource bounds and incomplete work
  becomes a gap/truncation signal.
- Canonical writes enqueue disposable derived maintenance instead of synchronously rebuilding the
  whole Vault. The Watcher or explicit rebuild drains the queue; stale/damaged indexes fail closed
  and current lexical queries use a bounded canonical fallback.

## Interfaces

- `knowledge_support` v3: twelve read-only operations covering search/recall, exact get, context,
  explain, verify, inspect, lineage, graph, Wiki, identity, and gaps.
- `knowledge_sink` v2: separate scope-bound mutation process; it is never registered by the default
  plugin and cannot mutate Legal Pack, sources, Authority, audit, arbitrary paths, exports, signing,
  or permissions.
- `law_support` v3: nine read-only official/private/federated operations in a separate process and
  storage boundary.

## Upgrade

New Vault:

```bash
deeplaw knowledge init --vault ./vault --name my-project --scope project
deeplaw knowledge autonomy verify --vault ./vault
```

Existing v0.7 Vault:

```bash
deeplaw knowledge snapshot create \
  --vault ./vault --output ./snapshot-before-v0.9
deeplaw knowledge snapshot verify --snapshot ./snapshot-before-v0.9
deeplaw knowledge autonomy migrate \
  --vault ./vault --backup ./pre-autonomy-v0.7-backup
deeplaw knowledge autonomy verify --vault ./vault
deeplaw knowledge autonomy rebuild --vault ./vault
```

The migration is additive and retains source-derived v0.7 Assets, Source IR/Tree, Proposal Inbox,
Workbench, and review governance in a separate compatibility partition. Rollback restores the
verified pre-autonomy Vault and retains the replaced v0.9 Vault in a sibling recovery directory.
See [`INSTALL_UPGRADE_ROLLBACK.md`](INSTALL_UPGRADE_ROLLBACK.md) before changing a real Vault.

## Compatibility and removals

- The frozen v1/v2 read contracts remain available for their corresponding historical Vaults;
  autonomous v0.9 Vaults advertise v3.
- The v0.7 proposal/review route is not deleted because it remains the correct boundary for
  deterministic source compilation and untrusted external imports. It is no longer presented or
  invoked as the default for ordinary Agent-derived memory.
- Historical v0.7 release manifests, evaluator fixtures, benchmark registries, and signed release
  evidence stay immutable. v0.9 uses the version-general release-manifest v2 contract.
- No remote service, team RBAC, CRDT core, Neo4j/Elasticsearch dependency, or large GUI was added.

## Known evidence boundary

The checked-in multi-domain Gold set is a non-secret development regression set. The offline dense
implementation has an exact model identity and is not represented as an external neural checkpoint.
No external system result, model-session outcome, held-out label, evaluator identity, or signature
was synthesized for this release. The machine claim gate remains closed until the complete external
protocol succeeds.
