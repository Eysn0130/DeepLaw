# DeepLaw v0.11.0 Living Wiki Compiler acceptance matrix

Status: release-gate traceability matrix; exact formal outcomes are bound after the tag workflow  
`commercial_release_eligible=pending_external_gates`  
`quality_protocol_eligible=true`  
`competitive_claim_eligible=false`

Only the exact-tag workflow's generated manifest may set
`commercial_release_eligible=true`, after every release-blocking row below has actual evidence.

This matrix traces all 48 mandatory acceptance items and all 28 required deliverables from the
Living Wiki Compiler master instruction. Runtime code, JSON Schema, SQLite migration/state,
tests and actual command results take precedence over documentation. `partial`,
`historical_evidence` and `not_executed` are never reported as passed.

External results are not predeclared by this file. For the published Release, the authoritative
run result is `commercial-release-manifest.json`, its referenced reports, and
`post-release-verification.json`; the workflow cannot create the formal Release unless the
three-platform, three-host and exact-artifact conditions below pass.

## Evidence map

- Source identity, Source IR, migration and evidence:
  `src/deeplaw/knowledge_store.py`, `src/deeplaw/knowledge_autonomy.py`,
  `contracts/source-ir.v1.schema.json`, `contracts/knowledge-identity.v2.schema.json`.
- Governed compilation:
  `src/deeplaw/compilation/`, `src/deeplaw/api/knowledge_os.py`,
  `contracts/source-compilation-*.schema.json`.
- Freshness and purpose-aware retrieval:
  `src/deeplaw/compilation/freshness.py`, `src/deeplaw/retrieval/purpose.py`,
  `contracts/source-freshness-report.v1.schema.json`,
  `contracts/purpose-aware-retrieval.v1.schema.json`,
  `contracts/knowledge-query-plan.v4.schema.json`.
- Projection and deterministic rebuild:
  `src/deeplaw/projection/builder.py`, `src/deeplaw/knowledge_autonomy.py`,
  `contracts/living-wiki-manifest.v1.schema.json`.
- CLI/MCP/host boundaries:
  `src/deeplaw/knowledge_cli.py`, `src/deeplaw/knowledge_mcp_server.py`,
  `src/deeplaw/knowledge_sink_mcp_server.py`, `src/deeplaw/mcp_server.py`,
  `benchmarks/hosts/run_no_model_host_acceptance.py`.
- Release binding:
  `benchmarks/release/`, `.github/workflows/commercial-gates.yml`,
  `.github/workflows/release.yml`,
  `contracts/commercial-release-manifest.v4.schema.json`.

## 48 mandatory end-to-end acceptances

| # | Status and acceptance | Exact implementation / contract or migration | Exact tests and observable evidence |
|---:|---|---|---|
| 1 | Verified — PDF, DOCX, PPTX, XLSX, EPUB and Markdown preserve bytes and create Source Revision | `src/deeplaw/knowledge_store.py`; identity-v2 tables; `contracts/source-ir.v1.schema.json` | `tests/test_source_adapters.py`, `tests/test_knowledge_identity_v2.py`; source add/show/verify receipts |
| 2 | Verified — compilation consumes persisted Source IR | `src/deeplaw/knowledge_store.py`, `src/deeplaw/compilation/coordinator.py`; Source IR and packet schemas | `tests/test_source_compilation.py`, `tests/test_knowledge_identity_v2.py`; packet binds `source_ir_compilation_id` and digest |
| 3 | Verified — every active Source Revision has a source page | `src/deeplaw/projection/builder.py`; Living Wiki manifest | `tests/test_source_compilation.py`; quality gate inspects exact source page |
| 4 | Verified — uncompiled is explicit and no summary is invented | `src/deeplaw/projection/builder.py`, `src/deeplaw/retrieval/purpose.py`; Query Plan v4 | `tests/test_source_compilation.py`; `source_summary_gap_honest` quality check |
| 5 | Verified — run identity/profile replay is idempotent | `src/deeplaw/compilation/coordinator.py`, `src/deeplaw/compilation/store.py`; unique run identity | `tests/test_source_compilation.py`; replay/status receipt |
| 6 | Verified — >32 outputs stage in bounded packets and commit once | `src/deeplaw/compilation/coordinator.py`; packet/plan/batch schemas | `tests/test_source_compilation.py`; 360-object CLI quality compilation |
| 7 | Verified — every run state can recover or abort safely | `src/deeplaw/compilation/coordinator.py` Saga and `src/deeplaw/compilation/store.py` migration | `tests/test_source_compilation.py`; status/explain/abort and injected projection/commit failures |
| 8 | Verified — staged objects are invisible before canonical commit | coordinator staging tables and admission | `tests/test_source_compilation.py`; pre-commit query is empty |
| 9 | Verified — Knowledge and Relation Revisions become atomically visible | one SQLite `BEGIN IMMEDIATE` canonical commit | `tests/test_source_compilation.py`; commit receipt binds both revision sets |
| 10 | Verified — projection failure preserves canonical Knowledge | `src/deeplaw/compilation/coordinator.py`, `src/deeplaw/projection/builder.py` | `tests/test_source_compilation.py`; injected failure state becomes `projection_pending` |
| 11 | Verified — projection retry completes | coordinator `resume` and projection workflow | `tests/test_source_compilation.py`; succeeded receipt and manifest |
| 12 | Verified — aliases resolve one active canonical entity | identity resolution in coordinator/autonomy core | `tests/test_source_compilation.py`, `tests/test_autonomous_knowledge.py` |
| 13 | Verified — ambiguous entities are not auto-merged | closed identity actions and unresolved identities | identity ambiguity tests; unresolved receipt |
| 14 | Verified — merge/split history is auditable | immutable autonomous events and identity lineage | `tests/test_autonomous_knowledge.py`; event-chain verification |
| 15 | Verified — new evidence revises an existing concept identity | semantic key/CAS coordinator | source compilation revise tests; receipt keeps stable `knowledge_id` |
| 16 | Verified — new Source Revision propagates only to dependencies | `src/deeplaw/compilation/freshness.py`; dependency tables | `tests/test_source_compilation.py`; refresh report and structural diff |
| 17 | Verified — withdrawal exits current admission | Source governance plus freshness propagation | compilation/retrieval tests; quality withdrawal query |
| 18 | Verified — multi-source synthesis becomes stale on one input update | synthesis input set/digest and freshness service | synthesis freshness tests; explicit stale gap |
| 19 | Verified — remaining evidence prevents erroneous total deletion | dependency propagation preserves valid refs | freshness/carry-forward tests |
| 20 | Verified — overview and index have distinct roles/content | `src/deeplaw/projection/builder.py` | `tests/test_source_compilation.py` and `index_overview_distinct` quality check |
| 21 | Verified — rich Entity/Concept/Claim/Procedure/Synthesis pages | `src/deeplaw/projection/builder.py`; Wiki manifest | `tests/test_source_compilation.py` |
| 22 | Verified — relations show title and stable ID | projection relation renderer | projection tests; local graph CLI receipt |
| 23 | Verified — 300+ objects remain discoverable through shards | deterministic shard builder | 360-object quality gate checks every object ID |
| 24 | Verified — FTS/dense/Wiki/Graph/Canvas/cache destructive rebuild | `src/deeplaw/knowledge_autonomy.py`, `src/deeplaw/projection/builder.py` | `tests/test_autonomous_knowledge.py::test_destructive_derived_state_deletion_is_rebuilt_deterministically`; quality gate |
| 25 | Verified — rebuild never calls a model | deterministic local rebuild path | rebuild tests; report records `model_used_for_rebuild=false` |
| 26 | Verified — default answer is compiled-first | `src/deeplaw/retrieval/purpose.py` answer policy | `tests/test_source_compilation.py` and `compiled_hit_ratio` quality metric |
| 27 | Verified — verify/quote/legal are evidence-first | purpose policy registry and Legal Pack boundary | purpose tests; quality quote/legal boundary cases |
| 28 | Verified — raw fallback is visible in Query Plan | `src/deeplaw/retrieval/purpose.py`; Query Plan v4 | `tests/test_source_compilation.py`; `source_fallback` gap and quality metric |
| 29 | Verified — uncompiled/stale are explicit gaps | retrieval/freshness services | purpose tests; quality lifecycle cases |
| 30 | Verified — answer drills to exact revision/fragment/locator | bounded source refs and quote SHA-256 | compilation plan validation and citation-validity quality check |
| 31 | Verified — query is read-only by default | `knowledge_support` and read-only store | MCP/CLI isolation tests; unchanged audit head on repeated queries |
| 32 | Verified — backfill begins as draft | query-backfill two-phase coordinator | backfill tests and draft schema |
| 33 | Verified — durable/reusable/novel gate blocks promotion | backfill validation policy | backfill negative-path tests |
| 34 | Verified — promoted backfill remains agent-derived and non-legal | origin/Authority commit policy | backfill tests; `legal_authority=false` |
| 35 | Verified — Agent cannot mutate authoritative originals | isolated `law_support` store/process | `tests/test_library_scopes.py`, MCP isolation tests |
| 36 | Verified — Agent cannot self-grant | grant owner path and sink policy | sink/MCP authorization tests |
| 37 | Verified — compiler grant cannot call ordinary mutation | grant operation allowlist | `tests/test_source_compilation.py`; quality unauthorized write rejection |
| 38 | Verified — path/secret/case-data boundaries fail closed | capture/plan validation and case-data confirmation | security, source compilation and autonomous tests |
| 39 | Verified — Python API/CLI/MCP share the same coordinator | `src/deeplaw/api/knowledge_os.py`, CLI and MCP thin adapters | `tests/test_source_compilation.py`, `tests/test_knowledge_mcp.py`, `tests/test_knowledge_sink_mcp.py`; deterministic fake-Agent E2E |
| 40 | Release-blocking external gate — Codex/Claude/OpenCode no-model | pinned host harness; host acceptance schema | `.github/workflows/commercial-gates.yml` fixes 0.145.0/2.1.220/1.18.8; formal manifest must bind passed report |
| 41 | Verified — deterministic fake Agent performs real compilation | `benchmarks/hosts/run_no_model_host_acceptance.py` and compiler API | fake-Agent tests plus host report verify-after-compile |
| 42 | Verified as not executed — real model tasks are not conflated | host report claim policy | report requires `model_task_acceptance=false`; release notes state `not_executed` |
| 43 | Verified — fresh/old Vault, snapshot/restore/rollback | knowledge migration/maintenance and compilation migration | migration, maintenance, distribution lifecycle tests |
| 44 | Release-blocking external gate — Linux/macOS/Windows × Python 3.11/3.12/3.13 | nine-cell workflow matrix and platform receipts | exact candidate required; zero failures/errors/skips in every cell; old CI is inadmissible |
| 45 | Verified and release-blocking — installed wheel core loop | distribution lifecycle and fresh-wheel harness | `benchmarks/verify_fresh_wheel.py`; formal post-release rerun |
| 46 | Verified locally and release-blocking — pytest/Ruff/diff | repository verification commands | exact commands and counts appear below and in platform JUnit reports |
| 47 | Release-blocking — exact wheel/commit/schema/migration/quality binding | manifest v4 plus repository/migration inventory and baseline comparison | assembler rejects mismatched bytes, frozen baseline, quality regression or performance regression; post-release verifier rebinds tag checkout |
| 48 | Verified — claims match implementation and limits | docs checks, claim policy, release notes | release tests; `competitive_claim_eligible=false`; explicit Not verified/Deferred/Not claimed |

## 28-source Authoritative Pack quality gate

The 28 identities are the signed Authoritative Pack, not general Living Wiki Source Revisions.
The matrix therefore records `source_revision_id=null` with the exact, truthful pack semantics
instead of minting fictitious general-Knowledge identities.

- Sanitized per-source record:
  `benchmarks/quality/v0.11-28-source-decision-matrix.json`.
- Contract:
  `contracts/authoritative-source-quality-decision-matrix.v1.schema.json`.
- Generator/validator:
  `benchmarks/quality/build_authoritative_source_matrix.py`.
- Snapshot archive SHA-256:
  `6ce04e7268ddf73d005c8fb72238cc0891e273c5d370c8e6bd4c1ab3a717757d`;
  restore inventory verified.
- Exact signed catalog SHA-256:
  `49cf75169726e18851897556617fad4132881614c3f6ab9c6b2a78d4f8524305`.
- Decision result: 13 `no_action`, 15 `reparse_source_ir`, all other decision classes 0.
- Two isolated rebuilds are byte-identical. Target database SHA-256:
  `ff4bc58e3a77585dccb8b22bd049b50612b0a8c85f7fb858551ea424021fbdc0`.
- Active release:
  `lawrel_1bee97015ee440c71ea993b083a89005`; prior release remains immutable.
- No source title, source text, original path, private path, private payload or case data is
  committed in the matrix.

The Living Wiki quality comparison separately freezes baseline commit
`42382b264f4297965c25aaac6e85619e9e0d49b7` and reproducible 0.10.0 wheel
`9bda60831e4380092c9a3bdb80103b5ec8abbf5a2be0adf6ffd57f61cfa46ca0`.
Baseline and candidate use the same runner, source bytes, query suite, budgets, hardware identity,
Python, SQLite and offline policy. Manifest v4 requires both
`quality_regression=false` and `performance_regression=false`.

## 28 required deliverables

| # | Deliverable | Implementation, contract/migration, tests, receipt and documentation |
|---:|---|---|
| 1 | Current capability/gap audit | `docs/LIVING_WIKI_ACCEPTANCE_REPORT_2026-07-30.md`; final deltas in this matrix and release notes |
| 2 | Living Wiki Compiler ADR | `docs/adr/0001-living-wiki-compiler.md`; implemented by `compilation/` and projection tests |
| 3 | Authority/status dimensions ADR | `docs/adr/0002-authority-and-status-dimensions.md`; enforced by autonomy schemas/migration and governance tests |
| 4 | Source-to-Knowledge Compiler | `src/deeplaw/compilation/coordinator.py`; packet/plan/batch/receipt schemas; source compilation tests |
| 5 | Compilation Run Saga | coordinator/store plus compilation core migration; state/fault/recovery tests and status receipt |
| 6 | Closed Compilation Plan Schema | `contracts/source-compilation-plan.v1.schema.json`; validator and negative contract tests |
| 7 | Dependency/freshness model | `src/deeplaw/compilation/freshness.py`; dependency tables; freshness schema/tests |
| 8 | Source update/withdrawal propagation | source governance plus refresh; structural diff/freshness/withdrawal E2E |
| 9 | Rich Living Wiki Projection | `src/deeplaw/projection/builder.py`; manifest schema; projection and 360-object quality E2E |
| 10 | Sharded indexes/local Canvas | projection shards/Canvas; deterministic hash/pagination/discovery tests |
| 11 | Compiled-First Retrieval | `src/deeplaw/retrieval/purpose.py`; purpose-aware contract; ranking/budget/quality tests |
| 12 | Purpose-aware evidence-first | policy registry; verify/quote/legal/fallback tests and Query Plan receipts |
| 13 | Controlled query backfill | draft/validate/promote coordinator; backfill schemas and negative tests |
| 14 | Stable Python API | `src/deeplaw/api/knowledge_os.py`; public API tests and shared-domain parity |
| 15 | Complete CLI loop | `src/deeplaw/knowledge_cli.py`; CLI lifecycle, source, compilation, query/context/verify E2E |
| 16 | MCP completion | read/write servers and v4 contracts; tools/list, stdio, isolation tests |
| 17 | Codex/Claude/OpenCode Compile Skill | two plugins plus OpenCode adapter; validators, host harness and pinned no-model receipts |
| 18 | Obsidian Bridge Contract | `contracts/obsidian-bridge.v1.schema.json`, bridge implementation/mock lifecycle tests, adapter docs |
| 19 | Tolaria Bridge Contract | `contracts/tolaria-bridge.v1.schema.json`, thin adapter/mock tests, adapter docs |
| 20 | Migration/snapshot/rollback | knowledge store/autonomy/maintenance/compilation migration; old-vault and lifecycle tests |
| 21 | Unit/integration/E2E tests | `tests/`; narrow regressions, public seams, failure injection, acceptance and release suites |
| 22 | Deterministic fake-Agent | fake compile implementation/report contract; real source-to-knowledge test |
| 23 | Opt-in real-host harness | host harness remains separate and never counts no-model as model acceptance |
| 24 | Benchmark protocol/fixtures | frozen Evaluation Protocol and Living Wiki suite/report schemas |
| 25 | Acceptance report | this exact 48/28 matrix plus manifest v4 and post-release report |
| 26 | Compatibility statement | `docs/LIVING_WIKI_COMPATIBILITY.md`, `docs/INSTALL_UPGRADE_ROLLBACK.md`, host docs |
| 27 | Real execution examples | README/CLI lifecycle and source-free deterministic quality runner |
| 28 | Unfinished/unprovable inventory | release notes Not verified/Deferred/Not claimed; competitive evidence list |

ADR/document-only deliverables do not invent a SQLite migration. Persistent behavior rows bind the
actual additive migration implementation and migration inventory; adapter/document rows mark
migration as not applicable while still binding code, Schema, tests, failure behavior and docs.

## Actual command gates

Local candidate commands:

```bash
uv lock --check
uv run --frozen pytest --strict-markers
uv run --frozen ruff check .
git diff --check
```

Additional deterministic gates:

```bash
uv run --frozen python -m benchmarks.living_wiki.run_quality_gate ...
uv run --frozen python -m benchmarks.living_wiki.compare_quality ...
uv run --frozen python -m benchmarks.release.verify_reproducible_build ...
uv run --frozen python -m benchmarks.verify_fresh_wheel ...
uv run --frozen python -m benchmarks.hosts.run_no_model_host_acceptance ...
```

The exact local counts and metrics are recorded in the final handoff and generated JSON reports.
The three-OS JUnit counts, host versions, wheel/sdist hashes, schema/migration inventories and
post-release install result are bound by the formal Release manifest rather than copied into this
precomputed document.
