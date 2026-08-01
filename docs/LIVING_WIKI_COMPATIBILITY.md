# Living Wiki Compiler compatibility

Status: v0.12.0 compatibility note, 2026-07-30.

## Version discipline

The package is `0.12.0`. The compiler is released only after the full release gate binds exact
candidate artifacts, migration identities, three-host evidence and three-OS results.

## Vault and migration behavior

- New autonomous Vaults install the additive `deeplaw.source-compilation-core/v1` tables.
- Existing autonomous Vaults install those tables idempotently through the explicit
  `knowledge autonomy migrate` reconciliation path.
- Legacy v0.7 Vaults still require the explicit `knowledge autonomy migrate --backup ...` path.
- Existing Source Revision, Knowledge Revision, relation, grant, audit and workspace identities are
  not rewritten.
- Snapshot/restore includes compiler tables and CAS artifacts. Rollback to the pre-autonomous
  backup removes the autonomous/compilation core and restores the legacy Vault.
- Compilation staging is never silently promoted during migration or restore.

The additive tables cover Runs, metadata, artifacts, usage, MCP idempotency receipts, packets,
batches, staged actions, identity candidates, output sets, source dependencies, revision
dependencies, Synthesis input sets, freshness events and backfill drafts. Contract/persistence
changes use v1 identities because they did not exist in the released schema; frozen historical MCP
and JSON contracts remain present.

## Public interfaces

- Existing CLI commands remain unchanged; `knowledge compile`, `knowledge query` and
  `knowledge backfill` are additive.
- `KnowledgeOS` is an additive Python facade. Direct internal store calls remain unsupported.
- `knowledge_support` advances to v5 and `knowledge_sink` to v4 for Semantic, Editor, Wiki, and
  Synthesis operations. Earlier contract files remain frozen for older consumers.
- Default Knowledge OS plugin behavior stays read-only. A compilation sink is a separate owner
  configuration with a narrower grant.
- `law_support` and official/user-private Legal Pack storage and trust roots are unchanged.

## Derived state

Old Wiki/FTS/dense/graph/community/Canvas output may be deleted and rebuilt. The richer projection
replaces obsolete generated paths by manifest ownership; it does not treat a derived file as
canonical. Rebuild does not require a model or network.

## Host/editor compatibility

Codex, Claude Code and OpenCode share the same compile Skill. Existing host manifests do not
silently enable mutation. The OpenCode file is an explicit example overlay and contains a
placeholder for an owner-created grant.

The Obsidian plugin and Tolaria integration harness are production adapter surfaces over the same
CLI/MCP domain services. They do not become canonical stores or permission systems. Existing
Markdown editing and reconcile behavior remains available; root policies determine where an editor
may write.

## Rollback

Before using the compiler on an existing Vault:

```bash
deeplaw knowledge autonomy snapshot --vault ./vault --output ./snapshot
deeplaw knowledge autonomy verify --vault ./vault
```

For a legacy migration, preserve the explicit pre-migration backup and use:

```bash
deeplaw knowledge autonomy rollback \
  --vault ./vault --backup ./pre-migration-backup --confirm
```

There is no destructive “downgrade” that rewrites committed compiler revisions in place. A code
rollback must either retain knowledge tables as unknown additive state or restore the exact prior
snapshot/backup.

## Known compatibility limits

- Exact-tag Linux/macOS/Windows and real-model evidence are supplied only by formal release
  artifacts; a source checkout alone does not prove them.
- Only the reviewed Codex semantic task is a release gate. Unknown hosts/models and real
  Claude Code/OpenCode model tasks are not claimed.
- A future contract change to an existing compiler table requires an explicit migration rather
  than `CREATE TABLE IF NOT EXISTS`.
