# DeepLaw v0.13.0-rc.1 source-candidate notes

Status: **not released**. No package version, tag, wheel, sdist, catalog or public release was
created. The repository continues to identify the latest released package as v0.12.0. These notes
describe the reviewed source candidate only.

## Evidence-complete compilation

- Semantic Profile v3 adds deterministic dynamic Duty applicability. Applicable unresolved and
  unknown duties block `complete`; `not_applicable` requires a bounded substantive reason.
- Statement Evidence core v1 persists stable statement identities, statement hashes/types,
  evidence maps, independent receipts and dependency state in the same semantic transaction.
- Source Compilation status v3 exposes exact freshness/dependency state and current store
  verification for Profile v3 while Profile v2 retains its exact response contract.
- Human edits and source successors invalidate only dependent statement mappings; invalid or stale
  factual statements cannot enter ordinary Query v6 answers.

## Living Wiki v3

- One projection authority owns all generated Wiki/Canvas paths through a recursively verified
  aggregate manifest and a crash-recoverable projection journal.
- Projection Profiles `minimal`, `standard` (default) and `full` are versioned and switch-clean.
  `standard` has no per-object Canvas; local graph/Canvas remain explicit on-demand views.
- Page Registry, Link Index and Stable Resolver provide stable multi-identity lookup, exact
  pagination, total counts, explicit truncation and no filesystem backlink scan on the v3 path.
- Coverage Specification and deterministic Gap artifacts govern page families, topics, duties,
  hierarchy, tours, codemap, page budgets and sharding.
- Wiki pages distinguish Source Evidence from Agent-derived Source Summary and expose semantic
  duties, revisions, lifecycle/freshness and statement-to-fragment drill-down.

## Query and persistent context

- Query Plan v6 is the source-candidate default across CLI, MCP and Python. It resolves targets,
  computes duties, selects admitted statements, performs duty-targeted evidence fallback,
  suppresses represented evidence and returns residual gaps under one item/character/token/source
  budget. Query Plan v5 remains explicitly selectable.
- `compact`, `standard` and `audit` capsule projections stay provider-bounded; full local audit is
  retrieved by receipt identity rather than included by default.
- The MCP lifespan retains a verified read-only Evidence/Knowledge snapshot and Wiki indexes.
  Warm reads compare cheap database, audit and manifest identities; changed state is invalidated
  before reopen/verification, and explicit verify remains full.
- Agent Context Envelope v1 carries bounded task/goal/repository/editor/tool-digest state across
  hosts without writing knowledge. CLI `knowledge agent-context` builds the exact same envelope.

## Hosts and operator flow

- Six explicit split Skills replace the monolithic default workflow: query, compile, verify,
  refresh, navigate and promote. Legacy wrappers remain on a deprecation schedule.
- Claude Code has an opt-in bounded no-model lifecycle adapter for prompt, compact, tool-digest,
  stop and session events. It never installs itself, reads transcripts, writes knowledge or grants
  capability.
- OpenCode defaults to the split read and compile Skills.
- Obsidian gains source/run/page pickers, exact Semantic v3 duties/status, Statement Evidence,
  paginated Wiki links, context preview and explicit begin/resume/refresh actions. The release
  bundle builds locally; signed real-desktop E2E is not executed.
- Tolaria uses the same Agent Context Envelope and public CLI/MCP domain seams. Its exact
  v2026-07-22 integration remains `integration_limited` because the public host lacks the required
  third-party preview/promotion UI extension point.

## Authoritative Pack

- A source-free read-only Authoritative Navigator exposes document/version/effective-date/segment,
  definition/cross-reference/warning/Gap/receipt identities without generating Official prose.
- All five warning-bearing public source identities are explicitly downgraded to
  `identity_locator_only`. Maintainer/expert review remains pending; exact quote and critical-token
  capability is not inferred.

## Compatibility and release decision

The changes are additive: Semantic v1/v2, Query v5, existing Legal Pack storage, read-only MCP
boundaries and v0.12 canonical data remain supported as documented in
[`V0_13_COMPATIBILITY.md`](V0_13_COMPATIBILITY.md).

The complete evidence decision is in
[`V0_13_RC_DISPOSITION.md`](V0_13_RC_DISPOSITION.md). Real Codex blind execution, Human Gold,
external exact 28-source rerun, real Obsidian/Tolaria desktop flow, 10k/100k performance and RSS,
the 3×3 OS/Python matrix, fresh/reproducible artifacts, SBOM, provenance and public re-download are
not executed. Consequently these notes do not announce v0.13.0 GA or a published RC, and
`competitive_claim_eligible=false`.
