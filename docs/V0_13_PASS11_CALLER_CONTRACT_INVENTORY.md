# DeepLaw v0.13 Pass 11 caller and contract inventory

Status: **frozen source-candidate inventory**, 2026-08-11. This is a preservation boundary for
Pass 11, not a deprecation notice, migration approval, qualification result, or release claim.
Package remains `0.12.0`; `release_ready=false`.

## Binding

The inventory was prepared on parent commit
`e422550ba23a448403381f34fa36cb2b82863eea` / tree
`205f63da00408dc79f4c58388d073409cd9cb383`. The following exact file hashes bind the Phase 2
candidate surface reviewed here:

| File | SHA-256 |
| --- | --- |
| `src/deeplaw/cli.py` | `dfd733a76b3ffe3f6e5b26852a49bc9e19cb994468fe97df6aa38cc53edb13d3` |
| `src/deeplaw/knowledge_cli.py` | `47d1fe1dd7de08030e356ab1c26e5b9359d359012e2b019758c11e0054569aa3` |
| `src/deeplaw/host_connect.py` | `c3f286fcbe0c1ecae1d40da098993880e0a5197aa8f56c3e716fd89d5bead438` |
| `contracts/host-connect-plan.v1.schema.json` | `d27d2467278337feddb6e7181689f3c5a3c96016e78c1bfdb7d4d14a2f5948c6` |
| `contracts/product-surface-manifest.v1.schema.json` | `ff37d495a9c4d7078a101abbacd0f8a225fc63223d9d6b0861333738940e2ca2` |
| `governance/product-surface-manifest.v1.json` | `454c3db37245f8b6ecf8ba0990066491cac1afe8ef6831b41ce62c693cc0df01` |

The machine-readable caller list, surface categories, exact bindings, mutation boundaries, and
deprecation prerequisites are authoritative within this inventory in
`governance/product-surface-manifest.v1.json`. This document explains the preservation decision;
it does not create a second manifest.

## Current contracts and callers

| Caller | Current contract or seam | Frozen compatibility family | Mutation boundary |
| --- | --- | --- | --- |
| Basic CLI | `init`, `doctor`, `source add`, `compile`, direct `reconcile`, `context`, `wiki`, `snapshot`, `forget`, `host connect` | all directly executable advanced/admin/compatibility commands | Owner mutation stays explicit; Help visibility does not remove a parser or persisted state |
| `knowledge_support` | input/output v6: `7996d4ada85fdd1dfa67fb2ee7bb734dcbf0e7d8d9a8b93cd6ce2ff083eee566` / `d68270871d4ca16b0e574744ffb1c18e66dba8c71c634ecfa1e4294ceb7ba95e` | input/output v1-v5 | read-only; no hidden write or grant widening |
| `knowledge_sink` | input v5/output v4: `fd4dbcb6ab75dca703643f4c09f7f10fa09eddcc63674ae017cb8a77040011f7` / `7256c68ec042ea328a2f2f8004400d9288a7a2a3c51acb87ac3aeb25083b6007` | input v1-v4; output v1-v3; active grants select v2-v5 input | separate explicit owner grant and process |
| `law_support` | input/output v4: `68b00d08db631ff7226abbc48ad8b4b8b5e78d6e5711e13b33d593b7f65899fe` / `235b348970784f2f13d04af366d9ee3d3971b1ed4e0a3defd0e8f0c658a634cd` | input/output v1-v3 | separate read-only evidence process and store |
| Host adapters | owner-side `host-connect-plan/v1` plus existing static adapters | current Codex/Claude Code/OpenCode fixtures and compatibility wrappers | thin mapping only; no auth, runtime, retrieval, governance, persistence, or Sink ownership |
| Plugins and Skills | explicit current split Skills and read-only MCP registrations | `use-knowledge-assets`, `compile-living-wiki` | text/manifests cannot grant capability or Authority |
| Persisted state and receipts | Source Revisions, Markdown Revisions, Ledger events, snapshots | v0.6/v0.7 migration fixtures, legacy CAS layout, historical schema receipts | no deletion without migration, recovery, audit replay, rollback, and owner approval |

## Help and compatibility disposition

Default Help is a product-story projection only. `--help-advanced`, `--help-admin`, and
`--help-compatibility` reveal the corresponding inventories. The underlying `argparse` choices
remain registered, so direct old commands continue to execute. `deeplaw knowledge reconcile` is an
alias over the existing autonomous reconcile/commit coordinator and creates no second write path.

No legacy schema, alias, receipt, migration fixture, or persisted state is deleted or deprecated in
Pass 11. A future removal still requires exact caller evidence, historical-data migration and
recovery evidence, rollback rehearsal, synchronized documentation/tests, and explicit Owner
approval.

## Unresolved callers and release effect

Repository tests and static adapters prove compatibility only at local public seams. Real Host
configuration resolution, installed-wheel qualification, unknown external callers, and historical
user Vault inventories are not complete. Therefore this inventory supports preservation, not
removal. It grants no qualification status and does not change the package classifier.
