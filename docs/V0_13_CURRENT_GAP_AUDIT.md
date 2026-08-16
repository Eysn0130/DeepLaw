# DeepLaw v0.13.0 current gap audit

Audit status: **pre-construction factual baseline**, 2026-08-07.

This file is intentionally preserved as the before-state. It is superseded for final status by
[`V0_13_RC_DISPOSITION.md`](V0_13_RC_DISPOSITION.md); a row below records a reproduced baseline
gap even when the source candidate later closes it.

Classification: **target-architecture migration**. Runtime code, contracts, migrations and tests
are authoritative for current behavior. This audit does not present the v0.13 target as shipped.

## Baseline inventory

| Fact | Observed value |
|---|---|
| Branch | `codex/semantic-evidence-package-fix` |
| Baseline / `origin/main` | `6736d994a6f3183821689f35471cf3958899fc27` |
| Exact tag | `v0.12.0` |
| Git tree | `cd1f11ae31095af0db2f58af4285d98acbe84ef2` |
| Package version | `0.12.0` |
| Working tree before audit docs | clean |
| Tracked files | 635 |
| Collected tests | 900 |
| Baseline suite | 897 passed, 3 skipped in 187.52s |
| Baseline skips | historical v0.6 wheel unavailable; two native Windows ACL/junction tests on macOS |
| JSON Schemas | 203; all validate as Draft 2020-12 |
| `uv.lock` SHA-256 | `0844135d38788f613b59fe8c99d18da82c6c8cb1d80c079c3349f11881815597` |

The baseline is the current `main` commit even though the local branch has a historical feature
name. No old tag was checked out and the package version was not changed during this audit.

## Persistence and migration inventory

- Legacy Knowledge Vault: `deeplaw.knowledge-vault/v1` and `deeplaw.knowledge-sqlite/v1`, with
  explicit backup/verification/rollback and identity-v2 migration paths in `knowledge_store.py`.
- Autonomous core: additive `deeplaw.autonomous-knowledge-core/v2` installed into v3/v4-named
  STRICT tables in `knowledge_autonomy.py`; the v1→v2 path adds semantic keys, relation temporal
  columns and grant fields while preserving audit history.
- Compilation core: `deeplaw.source-compilation-core/v1` plus
  `deeplaw.semantic-compilation-core/v1`, with Run/staging/dependency/Synthesis and Semantic v2
  tables installed and reconciled by `compilation/store.py`.
- Legal Pack: current release/storage `deeplaw.release/v3` / `deeplaw.sqlite/v6`, retaining
  v2/v5 compatibility and explicit evidence-capability migration/rollback in `store.py`.
- Migrations are embedded, additive SQLite installers rather than a standalone migrations
  directory. Any v0.13 persistence change therefore needs an explicit new schema identity,
  additive reconciliation, snapshot/restore/rollback and audit replay coverage.

## Confirmed P0 gaps

| Area | Current evidence | Risk classification | Required closure |
|---|---|---|---|
| Projection authority | `AutonomousKnowledgeStore.rebuild_derived` emits a legacy Wiki/Canvas set and then invokes `projection.builder.rebuild_living_wiki`; two manifests describe overlapping `wiki/` and `canvas/` outputs | P0 integrity/ownership | one projector, one ownership contract, safe cleanup and profiles |
| Projection scale | the rich projector unconditionally emits one Canvas per admitted object in addition to global/kind/community Canvas | P0 scale/reliability | default `standard` without object Canvas; on-demand local views |
| Persistent MCP | the MCP lifespan stores only `vault_path` and a lock; each request reopens Evidence/Knowledge stores and performs full integrity verification | P0 latency/freshness design | persistent integrity-bound snapshot and state invalidation |
| Wiki backlinks | `WikiReadService` scans at most the first 1000 Markdown pages and reports no scan-boundary receipt | P0 correctness/observability | complete Link Index with pagination and total count |
| Wiki resolution | page reads require callers to know a `wiki_path`; no stable multi-identity resolver or complete page registry exists | P0 usability/correctness | Page Registry and Stable Resolver |
| Wiki lookup compatibility | `wiki_lookup` is grouped with ordinary recall; only `operation=wiki` reads generated Wiki pages | P0 interface ambiguity | recommended path plus explicit deprecation metadata |
| Semantic false complete | Semantic v2 marks only six Duties `required`; unresolved Entity, Concept, Event, Procedure, Comparison or Typed Relation duties can coexist with `semantic_status=complete` | P0 semantic correctness | v3 dynamic applicability and no-false-complete rule |
| Statement evidence | Synthesis input sets and object-level source refs exist, but there is no persisted statement identity/map/receipt or human-edit invalidation | P0 grounding | statement-level contracts, persistence, admission and staleness |
| Duty-complete query | Query Plan v5 reports eight duties, but selection is object-level and a selected Synthesis does not trigger deterministic fallback for every uncovered v0.13 duty | P0 answer completeness | Query Plan v6 statement selection and targeted evidence completion |
| Query default drift | `PurposeAwareRetrievalService.query` and `handle_knowledge_support` default to Query Plan version `4` while v0.12 documentation describes v5 as the current shared path | P0 contract drift | one default version across CLI/API/MCP with v5 compatibility |
| Benchmark coupling risk | product cross-language phrase aliases include frozen-corpus-specific concepts such as diagnostic-log retention, Atlas release, badge color and scheduled publication | P0 evaluation validity | versioned general rules, exact-overlap audit and ablations |
| Agent instructions | current autonomous MCP instructions state safety/authority but do not put the v0.13 recommended read path in the first 512 characters | P0 host usability | concise recommended-path instructions and split Skills |
| Context lifecycle | Editor Context v1 is ephemeral, but it is editor-specific and does not cover the complete host-neutral task/commit/tool-digest lifecycle or compact recovery hooks | P0 continuity | Host-neutral Context Envelope and opt-in lifecycle adapters |
| Obsidian/Tolaria | bridges and deterministic harnesses exist, but current core flows still expose ID/path-oriented operations and do not prove the full v0.13 no-manual-ID product sequence | P0 product acceptance | product E2E and exact-version/limited integration evidence |
| Authoritative warnings | v0.12 truthfully retains five parser review warnings; critical-token independent transcription is not confirmed | P0 authoritative quality | independent verification or capability downgrade |
| Real host/Human Gold | v0.12 real Codex/Claude/OpenCode semantic runs are `not_executed`; Human Gold was `not_required`, which does not satisfy the frozen v0.13 GA gate | P0 release gate | real isolated Codex runs and real human-confirmed Gold, or RC/not released |

## Cross-project and stale-description findings

The repository still contains named external-project historical documentation, safety messages
and tests. The safety boundary itself is valid, but product-facing wording must become generic
case/client-data language and historical records must be clearly scoped instead of making another
product part of DeepLaw's current identity. Several current docs also retain “working tree” labels
after v0.12.0 was tagged. These are documentation truth defects, not permission to rewrite frozen
historical release evidence.

Synthetic tests intentionally contain example POSIX/Windows paths to prove leak rejection. Those
fixtures are not private local paths and must not be removed merely because a static search finds
them.

## Evidence still to collect before fixes

1. Minimal failing tests for every confirmed P0 rather than prompt-driven speculative patches.
2. Exact manifest/file call graph and time-admission difference between the two projectors.
3. Two-request verification/open counters and first-request invalidation behavior.
4. A 1001+ page backlink fixture and a large Canvas/file-count fixture.
5. A Semantic v2 false-complete fixture and a multi-statement object-level-evidence fixture.
6. A compiled-first query with a selected Synthesis but missing definition/exception/temporal duty.
7. Product/Benchmark exact phrase overlap and held-out ablation inventory.
8. Exact current upstream versions/commits and licenses for the bounded external research set.

No v0.13 capability is considered implemented by this audit. External model, Human Gold,
cross-platform, public release and expert review evidence remain `not_executed` or
`review_pending` until independently produced.
