# DeepLaw v0.13 source-candidate compatibility statement

Status: **not released**. The repository package version remains `0.12.0`; this document describes
the additive v0.13 working-tree contract and must not be read as a published version promise.

## Contract compatibility

| Surface | Current default in the source candidate | Compatibility retained | Boundary |
| --- | --- | --- | --- |
| Semantic compilation | `living-wiki-agent/v3` | Profile v1 and v2 inputs/status remain readable | v3 adds dynamic applicability, Statements and exact freshness/verification status |
| Query | Query Plan v6 | Query Plan v5 remains explicitly selectable | v6 no-answers legacy content without admitted Statements; callers needing the old object-level result must request v5 |
| Expansion | expansion Profile v2 | v1 receipts remain valid | v2 removes benchmark-shaped aliases and binds a generic lexicon digest |
| Projection | `standard` Profile, Living Wiki manifest v2 paired with Registry/Link/Resolver manifest v3 | aggregate manifest v1 and Profile `full` remain readable | `standard` removes per-object Canvas; profile changes clean only verified owned files |
| Evidence grounding | Statement/Evidence core v1 | source/object references remain readable | statement-bearing Profile v3 content must pass exact map/receipt verification |
| MCP read surface | default `query_plan_version=6`; recommended `query/context/wiki/source/verify` | v5 query and scheduled `search`/`recall`/`wiki_lookup` routes remain | `knowledge_support` stays read-only and provider output stays hard-bounded |
| MCP mutation surface | unchanged separate `knowledge_sink` | existing owner grants remain subject to their exact allowlist | no read operation hides a write or widens a Grant |
| Agent context | host-neutral Agent Context Envelope v1 | Editor Context Envelope v1 remains accepted for editor-specific context | both are ephemeral; neither is evidence or canonical knowledge |
| Legal Pack | unchanged isolated `law_support` store/process | current signed release and private-reference compatibility remain | v0.13 Navigator is a source-free read-only view and never creates Official prose |

## Persistence and forward migration

The candidate adds tables and markers in place; it does not rewrite v1/v2 records:

- `deeplaw.semantic-compilation-core/v1` remains the existing semantic persistence marker; Profile
  v3 uses additive versioned artifacts and records through the existing compilation transaction;
- `deeplaw.statement-evidence-core/v1` explicitly identifies Statement, map, receipt and dependency
  persistence;
- Living Wiki v3 Registry/Link/Resolver artifacts are derived and rebuildable; they are not a
  second canonical database;
- Query Plan v6 and Agent Context Envelope v1 are request/artifact contracts and add no canonical
  persistence.

The executable migration regression simulates a pre-Statement v0.12 database, reopens it through
the additive installer, verifies preserved audit heads, verifies both stores, snapshots the Vault,
restores it, and confirms v5 plus the new Statement marker. Formal G04 still requires the exact
release wheel and rollback artifacts; that release-bound gate is `not_executed`.

## Host compatibility

- Codex and Claude Code package the same six explicit split Skills. Claude lifecycle hooks are
  opt-in, no-model, bounded, network-free by code path and do not install themselves.
- OpenCode defaults to `deeplaw-query` and its compiler profile to `deeplaw-compile-source`; the
  two legacy wrapper names are not current defaults.
- Obsidian targets the public plugin API package `1.13.1` and exact upstream API commit
  `cc1744324150c632416857c98964f87b1574a5fc`. Its CLI bridge has closed parsers and no manual-ID
  core picker flow; real desktop E2E remains `not_executed`.
- Tolaria targets `v2026-07-22`, commit
  `e2cd718a518cc96d1081b6ec3aabefe3b6c77199`. Its external MCP boundary is supported, while the
  missing third-party preview/promotion UI seam keeps the product result `integration_limited`.

## Rollback and downgrade

Before any future release upgrade, the owner must create and verify a Vault snapshot. A downgrade
must use that snapshot or the prior exact release artifact; it must not delete additive tables or
rewrite audit history in place. Derived Wiki/index artifacts may be discarded and rebuilt by the
selected older compatible runtime. Query callers may explicitly request v5 while the v6 path is
being evaluated, but Profile v3 Statement-bearing writes must not be represented as v2 semantic
completeness.

## Not verified

Linux/macOS/Windows × Python 3.11/3.12/3.13, fresh-wheel migration, exact release rollback,
reproducible wheel/sdist, SBOM, provenance, public re-download, real Codex blind tasks, Human Gold,
real Obsidian desktop E2E and real Tolaria desktop integration remain external or release-bound
gates. No compatibility claim in this document converts any of them to `pass`.
