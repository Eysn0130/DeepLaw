# DeepLaw v0.13 source-candidate disposition

Decision: **source candidate complete; not released**.

DeepLaw remains package version `0.12.0`. No v0.13 tag, wheel, sdist, catalog, published RC, GA
release or public asset was created. This is the only release-safe outcome because real Codex blind
execution, Human Gold, the exact external 28-source run, mandatory desktop and cross-platform
release gates, 10k/100k scale, and 10,000-request RSS evidence are absent. The implementation is
usable for continued review, but `release_gate_passed=false`, `claim_eligible=false`, and
`competitive_claim_eligible=false`.

## Candidate binding

| Item | Exact binding |
|---|---|
| Baseline | `6736d994a6f3183821689f35471cf3958899fc27` (`v0.12.0`) |
| Implementation commit | `2bee4039dd63fa10c805e94ef57c82b919851f36` |
| Implementation tree | `bce4245f711cdcc4bb0eab923723f2ff8dfe42d9` |
| Package version | `0.12.0` |
| Frozen acceptance matrix | `d993b043bf0be5f4ac2e7b7aa523548c48ce1a66b5c6b0c78bc3a6149b7bb768` |
| Local scale report | `benchmarks/v013/scale-performance-local-2026-08-07.json` |
| Scale report SHA-256 | `36dd5eb7638459a4e67bedca6be8f6fce8354e3c5471e7c1b863e93082a0df9c` |
| Evaluation freeze reset | `2026-08-07T21:33:27Z` |

The scale artifact was generated from the clean implementation commit above. The final handoff
commit contains this disposition, the collaboration-ledger closure and that generated artifact;
its exact hash is reported by Sol in the Git handoff because a commit cannot contain its own hash.

The repository development and public temporal-holdout source hashes were re-frozen after required
documentation corrections. Therefore this implementation commit is the freeze commit, not a
strict descendant of it. The current tree cannot make the formal Evaluation Protocol claim; a
future unchanged candidate commit must strictly descend from this freeze and rerun the full
protocol.

## Required deliverables

| # | Deliverable | Evidence and disposition |
|---:|---|---|
| 1 | Current gap audit | `V0_13_CURRENT_GAP_AUDIT.md`; preserved as the factual before-state. |
| 2 | Agent collaboration ledger | `work-plans/V0_13_AGENT_LEDGER.md`; Task Cards, boundaries, Worker results and Sol reviews recorded. |
| 3 | Architecture duplication report | `V0_13_ARCHITECTURE_DUPLICATION_REPORT.md`; one projector and generic case/client boundary confirmed. |
| 4 | Semantic Profile v3 | Additive contracts/runtime/tests implemented; v1/v2 remain accepted. |
| 5 | Dynamic Duty applicability | Deterministic applicability, unknown blocking and substantive `not_applicable` reasons implemented. |
| 6 | Statement Evidence model | Statement, map and receipt persistence, replay, stale dependency and human-edit invalidation implemented. |
| 7 | Query Plan v6 | Default-aligned CLI/MCP/Python path, statement selection, targeted fallback, dedup, Gaps and receipts implemented; v5 selectable. |
| 8 | Persistent MCP Runtime | Verified persistent snapshot, cheap identity, bounded cache, first-read invalidation and receipt lookup implemented. RSS gate remains absent. |
| 9 | Wiki Page Registry | Additive v3 registry, stable IDs, revision/audit/hash bindings and sharding implemented. |
| 10 | Wiki Link Index | Complete indexed backlinks/outlinks, cursor, counts and explicit truncation implemented. |
| 11 | Stable Page Resolver | Multi-identity, admission-aware, ambiguity/staleness-safe resolver implemented. |
| 12 | Wiki Coverage Specification | Closed governed coverage input and four deterministic Gap classes implemented. |
| 13 | Projection Profiles | `minimal`, default `standard`, and `full` are versioned, manifest-bound and switch-clean. |
| 14 | Incremental Projection | Hash-incremental change sets, dry run, safe ownership cleanup and crash journal implemented. |
| 15 | Authoritative Navigator | Source-free read-only identity/capability navigation implemented; no Official prose generation. |
| 16 | 28-source quality report | Negative/capability report delivered; exact external Pack rerun is `not_executed`. |
| 17 | Parser Warning closure | All five warning-bearing sources downgraded to `identity_locator_only`; no false quote capability. |
| 18 | Real Codex blind report | Negative report delivered; real external blind execution is `not_executed`. |
| 19 | Human Gold manifest | Absence manifest delivered; reviewer identity is null and status is `review_pending`. |
| 20 | Context lifecycle adapters | Host-neutral ephemeral envelope plus opt-in Claude lifecycle and CLI surface implemented. |
| 21 | Split Skills | Six explicit read/compile/verify/refresh/navigate/promote Skills validated; grants remain external. |
| 22 | Obsidian product E2E | Local plugin tests/check/build/bundle pass; signed real-desktop E2E is `not_executed`. |
| 23 | Tolaria real integration report | Exact `v2026-07-22` bounded harness returns `integration_limited` and names the missing extension point. |
| 24 | Performance/scale report | Clean-commit 1k synthetic construction diagnostic executed; 10k/100k and RSS are `not_executed`. |
| 25 | Ablation report | Expansion/lexical/hybrid and source-bound calibration recorded; dense/graph are `not_executed`, hybrid is degraded. |
| 26 | Migration | Statement Evidence forward migration and v1/v2/v3 plus v5/v6 compatibility pass on local synthetic fixtures. |
| 27 | Rollback | Local snapshot/restore/rollback round trips pass; no published-artifact rollback was run. |
| 28 | Acceptance Matrix | Frozen before construction; exact SHA-256 retained. |
| 29 | Compatibility statement | `V0_13_COMPATIBILITY.md` distinguishes additive source-candidate behavior from released v0.12. |
| 30 | Known limitations | Recorded below and in the generated reports. |
| 31 | Not verified | Real models, external Pack bytes, signed desktop binaries, 3×3 platform matrix and public assets. |
| 32 | Deferred | 10k/100k/RSS profiler work, signed desktop runs, Human review and formal distribution lifecycle. |
| 33 | Not claimed | GA, published RC, competitive superiority, Human/Expert validation, legal correctness and SOTA. |
| 34 | Release notes | `RELEASE_NOTES_v0.13.0-rc.1.md` is explicitly labelled not released. |
| 35 | Formal release evidence | `not_executed`; no tag or distribution artifact exists to bind or re-download. |

## Frozen P0 disposition

`pass` below means the local source-candidate requirement has executable evidence. `partial` and
`not_executed` never count as release pass.

| Matrix rows | Status | Evidence / remaining boundary |
|---|---|---|
| A01–A04 | `pass` | Single projector, disjoint compatibility path, ownership-safe cleanup, journal recovery, profiles and switching. |
| A05 | `partial` | 1k `standard` fixture has 5 bounded non-object Canvas files; 10k/100k file-growth runs are absent. |
| R01 | `pass` | Two-request counters plus real low-level MCP cold/warm samples through one persistent lifespan. |
| R02–R04 | `pass` | Root/derived/Wiki/DB identity, withdrawal and sensitivity first-read invalidation, scope partition, closed key fields, 16-entry/1 MiB bounds and no restricted-result cache. |
| R05 | `partial` | Eight independent Readers and concurrent writer mutation pass; 10,000-request RSS is `not_executed`. |
| W01–W08 | `pass` | Registry, Link Index, resolver, coverage, page families, Source Evidence/Summary separation, incremental diffs and drill-down. |
| S01–S04 | `pass` | Additive v3, applicability, no-false-complete and deterministic inputs. |
| E01–E05 | `pass` | Contracts, migration, persistence, independent receipts, exact evidence and selective invalidation. |
| Q01–Q06 | `pass` | v6 default/compatibility, duty completion, admission, receipt visibility and bounded projections. |
| I01–I03 | `pass` | First-512 recommended path, explicit deprecation and Skills avoiding `wiki_lookup`. |
| B01–B02 | `pass` | Versioned generic expansion and static isolation from Benchmark/Gold. |
| B03 | `partial` | Source-free report exists; dense and graph variants are `not_executed`, hybrid lacks those channels, v5 calibration is not v6 Gold. |
| L01, L02, L04 | `pass` | Capability downgrade, read-only Navigator and honest pending human/expert status. |
| L03 | `not_executed` | Exact signed external 28-source fixture and independent critical-token review unavailable. |
| H01–H04 | `pass` | Closed no-write envelope, six Skills, Claude lifecycle and common OpenCode/Obsidian/Tolaria mapping. |
| O01 | `partial` | 12 plugin tests plus TypeScript check/build/bundle pass; signed desktop/model product E2E absent. |
| O02 | `pass` (limited branch) | Frozen row expressly permits proven `integration_limited`; real desktop/model workflow remains absent. |
| X01 | `pass` (catalogue) | Closed 44-metric catalogue defines denominator, scope, zero semantics, direction and absent Gold; measurements stay `not_executed`. |
| X02 | `partial` | All 60 scale/operation records exist, but only 17 1k operations executed; 43 records are `not_executed`. |
| X03 | `partial` | All five applicable 1k reference checks pass; no 10k/100k/RSS judgment exists. |

## Local scale/performance evidence

The report used Darwin `25.5.0`, arm64/12 logical CPUs, Python `3.12.13`, 24 GiB reported RAM,
4 KiB filesystem blocks, `standard` projection, Query Plan v6, 10 measured runs and 2 warmups.
It is a source-free synthetic construction diagnostic, not user-quality or competitive evidence.

| 1k operation | P50 ms | P95 ms | P99 ms | Status |
|---|---:|---:|---:|---|
| exact get | 0.934 | 1.106 | 1.106 | executed; no frozen target |
| Wiki page | 90.776 | 103.657 | 103.657 | pass (`≤150 ms`) |
| backlinks | 90.620 | 101.085 | 101.085 | pass (`≤150 ms`) |
| compiled-first | 100.621 | 110.295 | 110.295 | pass (`≤500 ms`) |
| MCP cold first request | 177.993 | 192.055 | 192.055 | executed; no frozen target |
| MCP warm request | 33.162 | 33.882 | 33.882 | executed; no frozen target |
| eight-reader probe | 533.979 | 584.853 | 584.853 | pass; 8 successful readers |
| provider payload | 171.482 | 181.900 | 181.900 | pass; 7,825 bytes, 0 hard-limit violations |

The 1k fixture recorded 8,429,568 SQLite bytes, 113 files and 5 Canvas files. Source update,
source-update cache invalidation and RSS were not executed by this report. Functional first-read
cache invalidation is covered separately by runtime regressions and is not relabelled as a timing
measurement.

## Formal GA gates

| Gate | Status | Reason |
|---|---|---|
| G01 | `not_executed` | No external blind corpus, exact real Codex execution or human-confirmed Gold. |
| G02 | `not_executed` | No real isolated compiler/evaluator pair for the v0.13 package. |
| G03 | `not_executed` | Local negative security/Authority regressions pass, but the required real blind counters do not exist. |
| G04 | `partial` | Local forward migration, snapshot, restore and rollback pass; no tagged distribution lifecycle is bound. |
| G05 | `not_executed` | No 3 OS × 3 Python no-skip matrix, fresh artifacts, reproducibility, SBOM/provenance or public re-download. |
| G06 | `partial` | Obsidian local bundle passes and Tolaria is honestly limited; signed real-desktop product evidence is absent. |
| G07 | `pass` | Required local lock, 1,102-test, Ruff, schema and diff checks pass; three declared environment/history skips are not hidden. |
| G08 | `pass` | `competitive_claim_eligible=false`. |
| G09 | `pass` | Mandatory unmet gates force this explicit not-released decision; no tag/release was made. |

## Exact local validation

| Command | Result |
|---|---|
| `uv lock --check` | pass; 140 packages resolved |
| `uv run --frozen pytest --strict-markers` | pass; 1,102 passed, 3 skipped |
| `uv run --frozen ruff check .` | pass |
| `git diff --check` | pass |
| Draft 2020-12 validation of `contracts/*.json` | pass; 239 Schemas |
| Acceptance Matrix SHA-256 verification | pass; exact frozen digest |
| Obsidian `npm test && npm run check && npm run build && npm run bundle:verify` | pass; 12 tests and verified 29.5 kB bundle |
| Tolaria cross-host/editor tests and integration harness | pass; harness status `integration_limited` |
| v0.13 migration/snapshot/restore/rollback focused tests | pass; 2 tests |

The three skips are exact and declared: unavailable historical v0.6 wheel, native Windows ACL,
and native Windows junction. They are why the local suite is not a substitute for G05.

## Exact external or expensive commands not executed

These commands were not executed for the reasons attached to each block. Placeholder tokens are
intentional requirements, not values silently substituted by local fixtures.

```bash
# Exact 10k/100k construction workloads; not run in this construction pass. The report retained
# the exact unshrunk workloads as unmet, and its required RSS child-process profiler is absent.
uv run --frozen python -m benchmarks.v013.scale_performance \
  --output <external-scale-report.json> \
  --scale 10000 --scale 100000 --query-runs 10 --warmup-runs 2 \
  --rss-requests 10000 --execute-expensive

# Exact external Authoritative Pack gate; signed catalog/source inputs are absent.
uv run --frozen python -m benchmarks.quality.run_authoritative_28_source_gate \
  --source-root <external-authoritative-source-root> \
  --catalog <signed-catalog.json> --catalog-signature <signed-catalog.sig> \
  --rollback-catalog <older-signed-catalog.json> \
  --rollback-signature <older-signed-catalog.sig> \
  --output <v0.13-quality-report.json>

# One of three required real Codex runs; external human-confirmed Gold/corpus/grant are absent.
uv run --frozen python -m benchmarks.hosts.run_semantic_host_harness \
  --host codex --host-version <exact-version> --model-identity <exact-model> \
  --grant-id <owner-created-bounded-grant> \
  --gold <external-human-confirmed-gold.json> \
  --corpus <external-blind-corpus-directory> \
  --vault <new-empty-temporary-vault> \
  --baseline-query-vault <new-empty-baseline-vault> \
  --command <exact-codex-command> --execute --output <run-1.json>

# Human review; a real independent reviewer has not supplied these inputs.
uv run --frozen python -m benchmarks.semantic.export_human_review_packet \
  --gold <external-v0.13-gold-candidate.json> \
  --compiler-report <external-blind-compiler-report.json> \
  --query-report <external-blind-query-report.json> \
  --output-directory <review-packet-directory>
uv run --frozen python -m benchmarks.semantic.review_gold \
  <reviewed-gold.json> --confirm --reviewer-id <real-reviewer-id> \
  --reason <review-record> --output <confirmed-gold.json>

# Each of Linux/macOS/Windows × Python 3.11/3.12/3.13 requires its own clean candidate,
# no-skip JUnit and passed distribution lifecycle report.
uv run --frozen python -m benchmarks.release.platform_gate \
  --junit <platform-python-junit.xml> \
  --lifecycle <platform-python-distribution-lifecycle.json> \
  --expected-system <Linux-or-Darwin-or-Windows> \
  --expected-python <3.11-or-3.12-or-3.13> \
  --output <platform-python-gate.json>
```

## Known limitations, deferred work and non-claims

- The autonomous schema marker remains additive rather than a package-version migration; v0.13
  behavior is source-candidate behavior inside a `0.12.0` package.
- Scope is immutable in the current Knowledge Object API. Scope cache partition is tested, but a
  forbidden in-place scope mutation is not fabricated as an invalidation scenario.
- The cache holds bounded public/private query/context provider responses only. Requests capable
  of admitting `restricted` content are never cached.
- The scale fixture counts source-derived Assets and uses bounded autonomous probes; it is not a
  100k real-user Knowledge Object corpus.
- Dense/graph comparative retrieval, real model token accounting, user-correction workflows and
  named same-condition competitor runs remain absent.
- Real Obsidian and Tolaria desktop UX, signatures, installers and model/session behavior remain
  outside local deterministic evidence.
- No maintainer/expert critical-token review was invented. Agent legal interpretation remains
  `legal_authority=false`.
- No quality, superiority, SOTA, production-readiness, legal-correctness, published-RC or GA claim
  is made.

## Final decision

The architecture and local source-candidate implementation are closed enough for review and future
external qualification. They are not evidence-complete for release. Sol therefore accepts the
implementation commit, rejects GA and published RC, leaves package version `0.12.0`, performs no
tag/release, and hands off a **not-released v0.13 source candidate**.
