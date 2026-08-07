# DeepLaw v0.13.0 acceptance matrix

Status: **frozen Release Gate specification**.

This file freezes the requirements for **DeepLaw v0.13.0 — Evidence-Complete Living Wiki &
Persistent Agent Context** before implementation. Its SHA-256 is recorded in
`docs/V0_13_ACCEPTANCE_MATRIX.sha256`. Construction may add evidence, but must not weaken, delete,
rename, or reinterpret these gates. A later evidence report may mark a row `pass`, `fail`,
`not_executed`, or `review_pending`; `not_executed` and `review_pending` never count as `pass`.

The version is a target-architecture migration from exact baseline
`6736d994a6f3183821689f35471cf3958899fc27` (`v0.12.0`, `origin/main` at freeze time). Package
version changes only after implementation and local acceptance are complete.

## Hard invariants

1. Evidence Authority remains exact CAS Source Revision or signed Authoritative Pack Release.
2. Knowledge body Authority remains the registered Markdown Knowledge Revision.
3. Identity and governance Authority remains the trusted Ledger.
4. Wiki, graph, FTS, dense, Canvas and caches remain deterministic disposable projections.
5. Task delivery remains a hash-bound Query Plan and bounded Knowledge Capsule.
6. `knowledge_support` remains read-only; `knowledge_sink` remains separate and owner-granted;
   `law_support` remains separately stored and read-only.
7. No score, link, model output, editor file or derived artifact can elevate Authority, scope,
   sensitivity, verification or capability.
8. Rebuild is offline, model-free, deterministic and cannot create semantic prose.
9. No user Vault, private legal source, case/client material, credential or signing key is used in
   development or committed evidence.
10. One concern has one canonical authority and one mutation path.

## P0 acceptance requirements

| ID | Required final state | Mandatory executable evidence |
|---|---|---|
| A01 | Exactly one default Living Wiki/Canvas projector owns every generated path and is called once by `rebuild_derived` | call-graph/ownership contract; duplicate-path rejection; manifest tamper tests |
| A02 | Legacy projection, if retained, is explicitly named, default-off, path-disjoint and absent from the Agent main path | compatibility test and generated-file inventory |
| A03 | Manifest cleanup deletes only previously owned stale files and is path-bounded, symlink-safe, crash-recoverable and dry-run observable | user-file preservation, symlink, crash and dry-run tests |
| A04 | `minimal`, `standard` (default) and `full` projection profiles are versioned, manifest-bound, migratable and switch-clean | Schema, forward migration, full/incremental rebuild and profile-switch tests |
| A05 | `standard` emits no per-object Canvas; local Canvas/graph are generated on demand; 1k/10k/100k file growth is reported | scale/file-count/Canvas tests |
| R01 | A persistent read-only runtime keeps verified Evidence/Knowledge snapshots and safe index handles across requests | two-request open/verify counters and cold/warm measurements |
| R02 | Every request checks cheap state identities; unchanged state avoids full verify; changed state invalidates caches and verifies before serving | data-version/audit/manifest mutation tests |
| R03 | Withdrawal, sensitivity, scope and manifest changes invalidate the first subsequent read without stale or cross-policy disclosure | four explicit invalidation regressions |
| R04 | Cache keys contain every admission parameter and never retain restricted bodies for lower-sensitivity requests | key-completeness and memory-content tests |
| R05 | Eight concurrent readers remain isolated; write lease remains independent; 10,000 reads have bounded RSS growth | concurrency, lease and RSS reports |
| W01 | Wiki Page Registry covers every generated page and binds stable page ID, canonical path, kind, current revision, audit head and hash | registry Schema/manifest/integrity tests |
| W02 | Wiki Link Index covers every outgoing stable target; backlinks/outlinks use the index, not filesystem scans | >1001-page exact completeness test |
| W03 | Link reads expose `total_count`, cursor, pagination and explicit truncation reason | contract and pagination tests |
| W04 | Stable Page Resolver accepts knowledge/revision/semantic/source/authoritative/wiki identities without path guessing | resolution success, ambiguity and stale-target tests |
| W05 | Governed Wiki Coverage Specification supports page families, topics, scopes, duties, hierarchy, tours, codemap, max pages and sharding | Schema, owner/draft governance and gap tests |
| W06 | Wiki v3 includes indexes, overview, recent changes, sources, typed objects, contradictions, gaps, communities and guides with explicit Authority, verification, freshness, lifecycle, semantic status, revision, evidence, history and gaps | page-family and contract tests |
| W07 | Source evidence and Agent-derived Source Summary are visually and semantically distinct | rendering and Authority-negative tests |
| W08 | Incremental projection changes only true dependants; page-level diffs and evidence drill-down are reproducible | successor/withdrawal/change-set tests |
| S01 | `living-wiki-agent/v3` is additive; v1/v2 remain accepted unchanged | Draft 2020-12 schemas and compatibility tests |
| S02 | Every Duty has deterministic `applicability` plus `status`; all applicable duties must be satisfied for `complete` | false-complete regression and applicability matrix |
| S03 | `unknown` applicability blocks complete; `not_applicable` requires a bounded substantive reason; empty/repeated output cannot satisfy a Duty | negative contract/finalization tests |
| S04 | Applicability is checked against media type, Source structure/IR, detected structure, observations and owner profile | deterministic validator tests |
| E01 | Versioned Knowledge Statement, Statement Evidence Map and Statement Evidence Receipt contracts persist statement ordinal/text/hash/type/support/input set and exact refs | Schema, forward migration, replay/integrity tests |
| E02 | Source Summary, Overview, Community Summary, cross-source Synthesis, Comparison and legal companion interpretation require statement-level mapping | per-kind enforcement tests |
| E03 | Factual statements require exact evidence; interpretations are labelled; unsupported statements cannot enter ordinary answers; contested statements include both sides or a Gap | admission and capsule tests |
| E04 | Each statement has an independent receipt and stable Wiki anchor; query can select statements without injecting a whole Synthesis | drill-down and payload tests |
| E05 | Source updates stale only dependent statements; human body edits invalidate inconsistent mappings until revalidated | incremental dependency and reconcile tests |
| Q01 | Query Plan v6 is additive and default-consistent across CLI, MCP and Python; v5 remains accepted | drift and v5/v6 compatibility tests |
| Q02 | v6 resolves target, computes applicable Duties, selects admitted compiled statements, measures coverage, performs only targeted evidence fallback, deduplicates represented evidence, recomputes coverage and emits residual gaps | end-to-end duty-completion tests |
| Q03 | Duties include primary answer, identity, definition, current state, temporal freshness, procedure, exception, contradiction, applicability, limitation, source evidence and unresolved gap | Schema and coverage tests |
| Q04 | Stale/invalidated/withdrawn/unsupported content is excluded; source-free knowledge remains interpretation/memory; no-answer returns a Gap | negative admission tests |
| Q05 | Every fallback, deduplication and suppression is receipt-visible and ranking cannot change Authority | receipt verification and tamper tests |
| Q06 | Provider projections `compact`, `standard` and `audit` retain hard limits; full audit remains local behind `receipt_id` by default | projection Schema and byte-limit tests |
| I01 | Recommended Agent read path is `query`, `context`, `wiki`, `source`, `law_support`, `verify` and appears within the first 512 MCP instruction characters | instruction and Skill contract tests |
| I02 | `search`, `recall`, `wiki_lookup` and mixed `plane=all` remain compatible but return deprecation metadata and a removal schedule | compatibility/deprecation tests |
| I03 | `wiki_lookup` no longer masquerades as Wiki page reading; current Skills do not select it by default | Skill and stdio tests |
| B01 | Query expansion is versioned, explainable and independent from Benchmark/Gold data | import-boundary and profile tests |
| B02 | Exact Benchmark phrase mappings are removed or generalized; Benchmark questions are not generated from product aliases | static comparison test |
| B03 | Frozen ablations cover expansion on/off, lexical, dense, graph, hybrid, compiled-first and evidence fallback with recall, precision, false positive, latency and token reporting | ablation report and held-out paraphrases |
| L01 | All five Authoritative Pack parser warnings are independently verified or receive an explicit Evidence Capability downgrade | warning-specific receipts and quality report |
| L02 | Authoritative Navigator exposes document/version/effective-date/segment/definition/cross-reference/warning/gap/receipt navigation without generating Official prose | derived-view and read-only tests |
| L03 | The 28-source gate separately reports retrieval, evidence-duty, citation/temporal and interpretation metrics; no citation fallback or false Authority is admitted | exact Pack gate and held-out report |
| L04 | Unconfirmed human/expert work remains `maintainer_review_pending` / `expert_review_pending` with no fabricated reviewer identity | Schema and policy tests |
| H01 | Host-neutral Agent Context Envelope is ephemeral by default, records no long-term knowledge and covers task/goal/workspace/commit/files/selection/tabs/note/tool digests/purpose/policy/budget | contract and no-write tests |
| H02 | Codex uses short split Skills for query, compile, verify, refresh, navigate and promote; knowledge content is not put in AGENTS.md | packaged Skill checks |
| H03 | Optional Claude Code lifecycle supports prompt/compact/recovery/stop/tool-digest semantics without secrets, network, automatic writes or Grant bypass | install/uninstall and no-model lifecycle tests |
| H04 | OpenCode, Tolaria and Obsidian use the same envelope and domain APIs | cross-host contract-drift tests |
| O01 | Obsidian core flow needs no internal ID entry and exposes source/run pickers, exact statuses, Duties, statement evidence, Wiki/source navigation, full links, context preview and explicit begin/resume/refresh | npm tests/check/build/bundle and product E2E |
| O02 | Tolaria exact release/commit harness covers active note → envelope → persistent query → preview → exact page → draft → explicit promotion → refreshed revision, or reports `integration_limited` with proven missing extension point | real desktop or bounded integration report |
| X01 | Retrieval, grounding, Wiki, context, Agent and Authoritative metrics define denominator, scope and Gold status and are reported separately | metric contract validation |
| X02 | Scale reports cover 1k/10k/100k exact get, Wiki links, compiled/evidence/context/verify/update/projection/MCP/concurrency/RSS/storage/files/Canvas/provider bytes with environment metadata | scale artifacts |
| X03 | Reference latency/RSS/hard-limit targets are met on the frozen environment or honestly fail with profiler attribution | performance report |

## Formal GA gates

| ID | Required final state | Pass rule |
|---|---|---|
| G01 | Real Codex host, exact model identity, repository-external blind corpus, human-confirmed Gold, three independent runs | all executed; no deterministic substitute |
| G02 | Blind compiler cannot read Gold/scorer/semantic keys/expected relations; evaluator cannot mutate compiled output | isolation receipt verifies both environments |
| G03 | Invented source, invalid locator/quote, unsupported factual statement, Authority elevation, wrong merge, restricted disclosure, unauthorized mutation, silent fallback, stale/withdrawn admission and false complete are all zero | every hard-failure counter is zero |
| G04 | Forward migration, snapshot, restore and rollback preserve evidence, identity, audit and v1/v2/v3 plus v5/v6 compatibility | exact migration lifecycle passes |
| G05 | Linux/macOS/Windows × Python 3.11/3.12/3.13, fresh wheel, reproducible wheel/sdist, SBOM, provenance and public re-download verification | exact-tag artifacts all execute with no mandatory skips |
| G06 | Obsidian bundle and Tolaria integration meet their product gates | exact artifacts and harness receipts pass |
| G07 | Required local commands pass: `uv lock --check`; `uv run --frozen pytest --strict-markers`; `uv run --frozen ruff check .`; `git diff --check` | exact final commit, no hidden skip |
| G08 | `competitive_claim_eligible=false` unless named same-condition competitor runs exist | false by default; only evidence can change it |
| G09 | No GA/tag/release when any real Codex, Human Gold, P0 or mandatory cross-platform gate is not executed or fails | unmet gate forces RC or not released |

## Required final disposition

The final Sol report must bind the base/final commit, P0 reproductions and fixes, every Worker Task
Card and independent review, architecture/Wiki/retrieval/runtime/Pack/host results, Schema and
migration identities, exact tests, performance environment, rollback, external evidence,
`not_executed`, known limitations, Human/Expert status, `competitive_claim_eligible`, and the
GA/RC/not-released decision.
