# DeepLaw v0.12.0 Semantic Living Wiki acceptance matrix

Status: **release-gate specification**. A row marked `Local verified` is backed by the named test,
but is not public-release evidence. Rows marked `Formal gate` pass only when the exact-tag
`commercial-release-manifest.json` and `post-release-verification.json` contain the corresponding
executed artifact. `partial`, historical evidence, a deterministic fake Agent, and
`not_executed` is never counted as passed for an applicable gate. The owner-approved v0.12 scope
does not make paid external real-model execution or human Gold review applicable release gates;
both remain explicitly recorded as `not_executed` / `not_required`.

The implementation baseline is `33228c99c4161c1730eb0f11e845d0d6011babf4` (`v0.11.0`). The
release commit, artifact hashes, nine-cell platform matrix, machine-review consensus, and public-download
verification are deliberately generated after the final clean commit to avoid self-reference.

## Semantic Gold correction sub-gates

These are mandatory before merge, tag, or Release. The canonical Gold remains
`machine_review_pending`; `human_gold_review.status=not_required`, `maintainer_confirmed=false`,
and `reviewer_id=null` are deliberate owner policy, not missing evidence.

| Sub-gate | Current implementation evidence | Pass rule |
|---|---|---|
| Cross-packet identity | 36-section case-01 fixture; deterministic lifecycle; scorer and tamper tests | At least two distinct Packets and observations converge to exactly one stable Knowledge ID |
| Entity/Concept scoring | Gold `scoring_policy`; Gold Schema; query runner/scorer tests | Target-scoped precision is named; valid extra objects are excluded; completeness and source coverage are separate |
| Security challenges | two adversarial fixtures; five frozen challenges; first-party CLI runner; tamper tests | Every challenge executes at least once and all nine security counters are zero |
| Claim-level content Gold | case-04, case-06, case-11, and case-12 content assertions | Concept, Source Summary, Synthesis, and Overview required content and exact input source set pass deterministic checks and six independent claim-entailment reviews |
| Contradiction applicability | retention A/B fixtures; structured `expected_relations`; query runner/scorer; provider relation regression | Same Atlas production service, ordinary public-API diagnostic-log class, worldwide scope, restricted-payload exclusion, distinct non-mergeable endpoints, 2026 valid interval, and provider-visible exact Relation Revision/titled endpoints before contradiction credit |
| Withdrawal gap | case-10 `explicit_gap` plus frozen `stale_knowledge` code | Withdrawn evidence is neither selected nor silently omitted; the current no-answer state remains visible |
| Reproducible freeze commitments | freeze Schema canonical-JSON profile, query-set projection, and per-field commitment profiles | A reviewer can independently reproduce every candidate, fixture-manifest, Schema, query-set, scoring-policy, and challenge digest without inferring mixed serialization rules |
| Cross-language retrieval | 14 natural-Chinese query variants; bounded query-only expansion; autonomous plan v1 and Query Plan v5 receipts | Every variant uses the canonical case targets/claims/citations/budget, runs cold and warm through the first-party CLI, and cannot change Authority or stored evidence |
| Timeline and multi-format | case-08 three Event labels; case-15 scheduled-publication Event | One formal valid-time interval per Event revision and every frozen date/object retrieved |
| Host declaration discipline | real-host report v2; deterministic workflow | Claude Code/OpenCode exact version/discovery/auth/model-access reason; external execution is `not_executed` |
| Independent audit | six isolated machine-review packets and bilingual derived Owner packets | Every auditor covers all 15 cases and five challenges; only six-way `CONFIRM` without material divergence passes |
| Owner release policy | Gold, freeze, consensus, and release manifest contracts | Human Gold review is `not_required`; no Agent creates a reviewer identity or writes `maintainer_confirmed` |

## 64 end-to-end acceptance requirements

| # | Status and invariant | Implementation / contract | Executable evidence |
|---:|---|---|---|
| 1 | Local verified — current v0.11 Vault upgrades without re-ingest | `src/deeplaw/compilation/store.py`, `knowledge_store.py`; additive compilation tables | `test_v011_compilation_check_domains_migrate_without_reimport`, `test_old_vault_migration_snapshot_restore_and_rollback_preserve_compilation` |
| 2 | Local verified — v1 Plan remains accepted | `compilation/coordinator.py`; `source-compilation-plan.v1.schema.json` | existing v1 compilation suite plus `test_cli_api_and_mcp_share_one_compilation_domain_result` |
| 3 | Local verified — Observation Plan v2 is closed | `compilation/semantic.py`; `source-compilation-observation-plan.v2.schema.json` | `test_semantic_v2_observes_across_packets_and_publishes_atomically` |
| 4 | Local verified — observations are staging-only | `compilation/store.py`, `finalization.py` | `test_compilation_batches_remain_invisible_until_one_atomic_commit` |
| 5 | Local verified — bounded later-packet inventory | `compilation/semantic.py`; `run-semantic-inventory.v1.schema.json` | `test_semantic_v2_observes_across_packets_and_publishes_atomically` |
| 6 | Local verified — one Entity across multiple packets | `compilation/finalization.py`, `knowledge_autonomy.py`; deterministic Semantic Gold Agent | semantic v2 atomic test; `test_semantic_case_01_uses_two_real_packets_and_one_stable_identity` and tamper test |
| 7 | Local verified — ambiguous same-name entities do not merge | semantic identity resolver; Observation/Publication v2 contracts | `test_compiler_reuses_exact_identity_and_preserves_explicit_ambiguity` |
| 8 | Local verified — aliases retain one canonical identity | same resolver and `knowledge-identity.v2` | `test_compiler_reuses_exact_identity_and_preserves_explicit_ambiguity` |
| 9 | Local verified — concepts fuse across chapters | run-wide inventory/finalization | semantic v2 cross-packet test and frozen Semantic Gold case |
| 10 | Local verified — every observation has one disposition | `semantic-publication-plan.v2.schema.json`, `finalization.py` | semantic v2 finalization test |
| 11 | Local verified — silent observation loss fails closed | inventory digest and exact set comparison | semantic v2 finalization and concurrent replacement tests |
| 12 | Local verified — Source Summary is a canonical Synthesis | `finalization.py`; semantic finalization/quality receipt contracts | semantic v2 test; real-host Formal gate |
| 13 | Local verified — summary claims bind exact evidence and expected content | source-reference validator, quality receipt, claim-level Gold | semantic v2 test; frozen content/source-set scorer, tamper checks, and six-agent machine consensus gate |
| 14 | Local verified — unsupported summary is rejected | 15-duty completeness validator | `test_empty_semantic_compilation_cannot_report_success` |
| 15 | Local verified — duty states are distinct | `semantic_duties.py`; `semantic-compilation-duty-report.v1.schema.json` | semantic v2 test and contract validation |
| 16 | Local verified — transaction success can coexist with semantic partial | separate run and semantic status columns/contracts | semantic partial/status/query tests |
| 17 | Local verified — Source Page shows transaction, semantic status, and duties | `compilation/projection.py` | source-page projection assertions in `test_source_compilation.py` |
| 18 | Local verified — query exposes partial-compilation gap | `query_plan.py`, `compilation/query.py`; Query Plan v5 | Query Plan v5 tests |
| 19 | Local verified — publication is one atomic commit | `compilation/finalization.py`, coordinator transaction | atomic semantic v2 test |
| 20 | Local verified — pre-commit content is invisible | staging tables and admission path | `test_compilation_batches_remain_invisible_until_one_atomic_commit` |
| 21 | Local verified — crashes recover or abort safely | coordinator recovery/CAS and abort | `test_compilation_recovers_before_and_after_atomic_commit`, abort test |
| 22 | Local verified — projection failure preserves canonical state | projection-pending state and retry | recovery tests and `test_projection_has_no_model_network_or_subprocess_dependency` |
| 23 | Local verified — refresh creates a new Overview revision | `compilation/synthesis.py`; synthesis refresh contracts | synthesis refresh saga tests |
| 24 | Local verified — successor stales old Overview | dependency propagation | successor and transitive synthesis freshness tests |
| 25 | Local verified — refresh restores freshness | synthesis refresh coordinator | synthesis refresh saga tests |
| 26 | Local verified — withdrawal invalidates or revises Synthesis | source removal and dependency propagation | `test_source_removal_invalidates_dependencies_and_recall` |
| 27 | Local verified — rebuild never calls a model | deterministic projection/rebuild modules | `test_projection_has_no_model_network_or_subprocess_dependency` |
| 28 | Local verified — fresh Synthesis is compiled-first and single-target noise, including unrelated stale/uncompiled gaps and internal overfetch/relevance-floor rejections, is suppressed; deleted legacy and autonomous FTS remain recoverable without admitting canonical damage | Query Plan v5 admission/target-scoped selection, suppressed-candidate receipt, bounded meaningful-term gap matching, and fail-closed derived rebuild preflight | `test_purpose_aware_query_is_compiled_first_and_read_only`; `test_uncompiled_source_gap_uses_all_meaningful_query_terms`; `test_v5_target_scoped_capsule_suppresses_internal_candidate_noise`; `test_search_index_rebuild_repairs_only_removable_integrity`; successor gap regression; frozen 15-case CLI suite |
| 29 | Local verified — source-free claims never become evidence | provenance partitions in Query Plan v5 | Query Plan and source-reference tests |
| 30 | Local verified — raw fallback is visible, exact historical evidence is governed, and source-byte tamper fails closed | Query Plan v5 fallback/gap/receipt; historical governance; current source-byte admission | raw/dense fallback, exact historical fragment, direct fragment tamper, and compiled-dependency tamper tests |
| 31 | Local verified — legal duties route to `law_support` | `query_plan.py`, separate Legal Pack store/process | Query Plan legal-route and MCP boundary tests |
| 32 | Local verified — query is read-only and restricted targets cannot receive public substitutes | shared retrieval service and non-identifying boundary-target admission | compiled-first read-only, restricted-target, and MCP tests |
| 33 | Local verified — backfill remains Draft→Validate→Promote | `knowledge_backfill.py`; four backfill contracts | `test_query_backfill_requires_draft_validation_and_explicit_promotion` |
| 34 | Local verified — Editor Context is ephemeral | `editor_bridge.py`; editor context contracts | editor-context tests |
| 35 | Local verified + Formal artifact gate — Obsidian builds and installs | `adapters/obsidian/plugin`; `obsidian-bridge.v1.schema.json` | `npm test`, `npm run check`, `npm run build`, `npm run bundle:verify` |
| 36 | Local verified — layout ready does not ingest the Vault | Obsidian `onLayoutReady` bridge | plugin lifecycle tests |
| 37 | Local verified — canonical/derived roots reject editor writes | `editor_bridge.py` path policy | parametrized editor write-policy tests |
| 38 | Local verified — Obsidian can begin a governed run | plugin command calls first-party CLI/MCP | plugin tests plus Source stdio tests |
| 39 | Local verified — status, gaps, and exact locator are visible | plugin commands and read-only context envelope | plugin tests and editor contract tests |
| 40 | Local verified — Tolaria merge preserves settings and creates owner-only output | `adapters/tolaria/setup.py`; POSIX mode/Windows native ACL | merge/config security tests, integration harness |
| 41 | Local verified — ordinary Tolaria note tools cannot write owned roots | bounded bridge intent and path policy | Tolaria/editor policy tests |
| 42 | Local verified — active note receives ephemeral context | Tolaria context mapping | Tolaria temporary-Vault harness |
| 43 | Local verified — `knowledge_support` remains one read-only leaf | `knowledge_mcp_server.py`; input/output v5 | MCP tool-list and stdio tests |
| 44 | Local verified — Sink cannot self-grant | grant administration stays owner CLI-only | Sink capability tests |
| 45 | Local verified — compiler grant cannot `remember` | semantic-compiler profile allowlist | compiler grant least-privilege test |
| 46 | Local verified — synthesis grant cannot mutate Legal Pack | separate process/store and operation allowlist | capability and law-process isolation tests |
| 47 | Local verified — provider-visible UTF-8 stays under 64 KiB | MCP projection hard limiter | provider error/budget tests |
| 48 | Local verified — Source/Wiki/Editor/Synthesis use real stdio | MCP servers and closed v5 contracts | autonomous support and Sink stdio tests |
| 49 | Local verified — autonomous CLI context/API/MCP share one purpose-aware Capsule v2 domain result | `api/knowledge_os.py`, `knowledge_cli.py`, MCP handlers and v5 input Schema; frozen v4 stays unchanged; untouched v0.7 Vaults retain the v1 compatibility compiler | `test_purpose_aware_query_is_compiled_first_and_read_only`; `test_knowledge_support_v5_extends_without_mutating_frozen_v2_to_v4`; `test_cli_api_and_mcp_share_one_compilation_domain_result`; legacy CLI lifecycle test |
| 50 | Local verified — v0.11 coordinator behavior remains | shared coordinator and compatibility schemas | full regression suite |
| 51 | Local verified + Formal gate — forward/snapshot/restore/rollback | additive migration, verified snapshot and rollback | old-Vault, tamper, distribution lifecycle tests |
| 52 | Formal gate — Linux/macOS/Windows exact commit | `.github/workflows/commercial-gates.yml` nine-cell matrix | exact-tag platform JSON/JUnit artifacts |
| 53 | Formal gate — Python 3.11/3.12/3.13 | same matrix | exact-tag platform JSON/JUnit artifacts |
| 54 | Local verified + Formal wheel gate — Semantic v2 from wheel | `benchmarks/verify_fresh_wheel.py` | fresh-wheel artifact result |
| 55 | Formal deterministic machine-consensus gate | `semantic-evidence.yml`, deterministic lifecycle/query suite, six isolated packet contracts | exact-commit lifecycle/query/cost, six unanimous packets, consensus, and bilingual Owner packet; external real-model execution remains `not_executed` and is not substituted by no-model evidence |
| 56 | Local verified — fake Agent is labelled deterministic only | `deterministic_fake_agent.py`; fake report contract | fake-Agent E2E and Semantic Gold tests |
| 57 | Local verified + Formal artifact gate — frozen legal set does not regress | repository/Legal quality runners, `run_authoritative_28_source_gate.py`, and `run_authoritative_evidence_gate.py` | exact-commit 28-source matrix with 23 `no_action`, 5 risk-driven `reparse_source_ir`, explicit parser-warning rate, plus exact-tag `authoritative-evidence-quality.json` |
| 58 | Local verified — Challenge Trace replays | `authoritative_challenges.py`; trace/replay contracts | deterministic replay and tamper test |
| 59 | Local verified — ranking cannot elevate Evidence Capability | `evidence_capabilities.py`, admission logic | evidence-capability tests |
| 60 | Local verified — unconfirmed expert data stays pending | held-out Gold review tool/schema | legal quality review tests |
| 61 | Local verified — missing named comparators force false | release claim policy and Semantic reports | release manifest/schema tests |
| 62 | Local verified — README separates Current from Target | README capability table and repository Gold | repository Gold documentation gate |
| 63 | Local verified — historical pre-release report is labelled | historical report banner and README official links | documentation regression test |
| 64 | Formal post-release gate — download/hash/Sigstore/provenance/install | `release.yml`, `post_release_verify.py` | public `post-release-verification.json` only |

## 38 deliverables

| # | Deliverable | Code / contract / tests / receipt / documentation |
|---:|---|---|
| 1 | Current-main factual audit | `docs/LIVING_WIKI_IMPLEMENTATION_AUDIT_2026-07-30.md`, this matrix, runtime/tests as truth |
| 2 | v0.11 evidence consistency | historical report banner, README exact-release links, documentation tests |
| 3 | Semantic Compiler Profile v2 | `compilation/profiles.py`; `semantic-compilation-profile.v2.schema.json`; semantic v2 tests; `LIVING_WIKI_COMPILER.md` |
| 4 | Semantic Duty Schema | `semantic_duties.py`; duty report schema; finalization tests |
| 5 | Observation Plan v2 | `compilation/semantic.py`; Observation Plan schema; cross-packet tests |
| 6 | Run Semantic Inventory | semantic service/store; inventory schema; inventory digest tests |
| 7 | Finalization Packet | `compilation/finalization.py`; finalization packet schema; failure-path tests |
| 8 | Publication Plan v2 | finalization coordinator; publication schema; atomic commit receipt |
| 9 | Cross-packet identity fusion | finalization resolver; knowledge identity schema; ambiguity/alias tests |
| 10 | Semantic completeness | duty evaluator and quality receipt schema; empty-output/partial tests |
| 11 | Synthesis Refresh Saga | `compilation/synthesis.py`; refresh plan/run/task schemas; recovery tests |
| 12 | Overview/community refresh | synthesis projection and deterministic Wiki rebuild; synthesis tests |
| 13 | Query Plan upgrade | `query_plan.py`; Query Plan v5 schema; 41 Query Plan tests |
| 14 | Source-free/interpretation partitions | Query Plan v5 and retrieval projection; provenance/admission tests |
| 15 | Source/Wiki/Editor/Synthesis MCP | `knowledge_mcp_server.py`; v5 contracts; real stdio tests |
| 16 | CLI | `knowledge_cli.py`; public command acceptance tests and evaluation query runner |
| 17 | Stable Python API | `knowledge_api.py`; shared-domain equivalence test |
| 18 | Coordinator modularity | `compilation/coordinator.py`, semantic/finalization/synthesis modules; recovery tests |
| 19 | Production Obsidian plugin | adapter source, manifest, bridge schema, npm tests/build/bundle receipt |
| 20 | Tolaria integration | adapter module/harness, bridge schema, merge/context tests |
| 21 | Host-neutral Agent evidence | deterministic lifecycle, truthful external-host `not_executed` reports, and `semantic-evidence.yml` packaging gate |
| 22 | Semantic Gold | Gold schema/candidate/freeze; target-scoped scoring; 14 frozen Chinese variants; explicit owner machine-consensus policy; bilingual derived Owner packet |
| 23 | Semantic machine-review consensus | six packet roles, unanimous consensus contract, claim/safety/coverage metrics, and no human-review impersonation |
| 24 | Authoritative Challenge Trace | authoritative challenge module, trace/replay contracts/tests, executable `authoritative-evidence-quality.json` release receipt |
| 25 | Evidence Capability Types | capability module/contracts and migration/rollback tests |
| 26 | Answerability metrics | Legal/Semantic quality scorers; unanswerable Query Plan checks |
| 27 | Citation Audit | citation module/schema and deterministic mutation test |
| 28 | Expert held-out infrastructure | held-out schema/candidate/review tool; remains unconfirmed unless externally reviewed |
| 29 | Authoritative Pack Core | `authoritative_pack.py`; core schema; ADR 0005; isolation tests |
| 30 | Migration/snapshot/rollback | additive stores, capability migration contracts, lifecycle tests/docs |
| 31 | Unit/integration/E2E | mandatory pytest, MCP stdio, fake Agent, real-host external gate |
| 32 | Cross-platform | nine-cell Actions matrix and exact platform receipts |
| 33 | Fresh wheel | wheel verifier and distribution lifecycle report |
| 34 | Reproducible build | reproducibility runner/report and exact distribution hashes |
| 35 | Acceptance matrix | this file; formal manifest binds its digest |
| 36 | Release notes | `RELEASE_NOTES_v0.12.0.md`; Implemented/Verified/Externally verified/Not verified/Deferred/Not claimed |
| 37 | Known limitations | release notes and manifest claim policy |
| 38 | Not verified/deferred/not claimed | release notes; `competitive_claim_eligible=false` |

## Required commands

```bash
uv lock --check
uv run --frozen pytest --strict-markers
uv run --frozen ruff check .
git diff --check
cd adapters/obsidian/plugin && npm test && npm run check && npm run build && npm run bundle:verify
uv run --frozen python adapters/tolaria/integration_harness.py
```

Formal release additionally requires the exact-tag nine-cell matrix, three official CLI no-model
lifecycle, six-way deterministic semantic machine consensus, the 28-source authoritative gate,
fresh-wheel quality, reproducible
wheel/sdist, OCI/supply-chain gates, Sigstore/GitHub provenance, and public-download reinstall.
The formal manifest must record `commercial_release_eligible=true`,
`quality_protocol_eligible=true`, and `competitive_claim_eligible=false` before publication.
