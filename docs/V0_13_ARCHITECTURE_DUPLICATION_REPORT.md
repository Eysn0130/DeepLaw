# DeepLaw v0.13 architecture duplication disposition

Status: **source-candidate review, accepted with compatibility surfaces retained**. This report is
an implementation audit, not release evidence. It binds the working tree that descends from
`6736d994a6f3183821689f35471cf3958899fc27`; the final commit is recorded in the v0.13 disposition
after the tree is committed.

## Decision

The v0.13 source candidate has one default projection authority, one Ledger-backed canonical
knowledge store, one recommended Query Plan path, and thin host adapters. Compatibility contracts
remain additive and explicitly named; they do not form a second current implementation.

| Concern | Canonical authority | Retained compatibility | Review result |
| --- | --- | --- | --- |
| Living Wiki and Canvas projection | `AutonomousKnowledgeStore.rebuild_derived` calls `projection.builder.rebuild_living_wiki` once | manifest v1 and projection Profile `full`; legacy per-object Canvas is default-off | no second default projector |
| Generated-file ownership | aggregate derived manifest v2 recursively binds the Living Wiki v2/v3 component; projection journal/change set owns cleanup and recovery | aggregate manifest v1 is read-only compatibility | user files are outside ownership inventory |
| Knowledge mutation | `AutonomousKnowledgeStore` is the Ledger/CAS transaction boundary; compilation, reconciliation, CLI and Sink call it through the shared domain services | v1/v2 schema readers and Query v5 do not add a write path | no editor or read-MCP canonical write |
| Agent read query | Query Plan v6 is the CLI/MCP/Python default | Query Plan v5 is explicit compatibility; `search`, `recall` and `wiki_lookup` are deprecated discovery routes | one recommended route: `query`, `context`, `wiki`, `source`, `verify` |
| Wiki identity/navigation | Page Registry v1, Link Index v1 and Stable Resolver v1 under the v3 projection manifest | path reads remain an address form, not identity | no filesystem scan in the v3 indexed read path |
| Evidence grounding | Statement v1, Statement Evidence Map v1 and Statement Evidence Receipt v1 | object-level source references remain for v1/v2 content | statement selection does not create Evidence Authority |
| Host context | `deeplaw.agent-context-envelope/v1` | `editor-context-envelope/v1` remains the editor-state compatibility input | both are ephemeral and neither writes the Ledger |
| Host integration | CLI/MCP domain APIs | Codex, Claude Code, OpenCode, Obsidian and Tolaria adapters | adapters do not import storage or retrieval internals for business logic |
| Authoritative legal navigation | `law_support` and the isolated Legal Pack store | source-free Authoritative Navigator is a read-only derived view | no general-Knowledge write or Authority promotion |

## Static and executable evidence

The review used these reproducible checks:

```bash
rg -n "def rebuild_derived|rebuild_living_wiki\\(" \
  src/deeplaw/knowledge_autonomy.py src/deeplaw/projection
rg -n "from (benchmarks|evals)|import (benchmarks|evals)" src/deeplaw adapters plugins
rg -n -i "$(printf '%s%s' 'Analy' 'tix')" . --glob '!.git/**'
uv run --frozen pytest -q \
  tests/test_v013_projection_ownership.py \
  tests/test_v013_projection_profiles.py \
  tests/test_v013_projection_recovery.py \
  tests/test_v013_projection_v3_integration.py \
  tests/test_v013_split_skills.py \
  tests/test_v013_cross_host_context.py
```

The third command constructs the retired name at runtime so it is not retained as repository
terminology. The first scan resolves the default call graph to
`knowledge_autonomy.py:AutonomousKnowledgeStore.rebuild_derived` →
`projection.builder.rebuild_living_wiki`. The second has no match: product and adapter code do not
import benchmark or Gold modules. The third has no match after the explicit cross-project cleanup.
The final disposition records the exact test count from the final tree rather than copying an
intermediate count here.

## Compatibility is not duplication

- Semantic Profile v1/v2 and Query Plan v5 remain accepted inputs because v0.13 is additive. They
  are never selected by the current default unless the caller explicitly requests them.
- Living Wiki v2 is the generated-page inventory and atomic file transaction paired with v3's
  Registry/Link/Resolver indexes. This is one projection transaction with two bound contracts, not
  two projectors.
- `editor-context-envelope/v1` describes editor UI state. The newer Agent Context Envelope binds a
  host-neutral task/goal/repository lifecycle. Neither is canonical knowledge.
- `law_support`, `knowledge_support`, and `knowledge_sink` remain separate processes because their
  storage and capability isolation is a trust boundary, not permission to duplicate identity or
  Authority semantics.

## Known limitations

- The compatibility wrappers `use-knowledge-assets` and `compile-living-wiki` remain packaged on a
  removal schedule. Current Skills and OpenCode defaults do not select them.
- Query-ablation evidence remains intentionally bound to Query Plan v5 calibration; it is not a
  v6 superiority claim.
- Tolaria's current public extension boundary cannot implement the complete product UI loop; the
  harness reports `integration_limited`.
- This report cannot establish real-host behavior, Human Gold quality, cross-platform packaging,
  or release reproducibility. Those are independent gates in the final disposition.
