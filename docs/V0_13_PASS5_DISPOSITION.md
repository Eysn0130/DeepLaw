# DeepLaw v0.13 Pass 5 disposition

Status: **current source candidate stabilized locally; independent product qualification and
release qualification remain incomplete**.

## Candidate and scope binding

- Starting commit/tree: `b83016d92e2e74a34b753330f2f1e8a0452e8a74` /
  `e727b5f01526633c721477b5fb36b01cad9bab57`.
- Accepted implementation commit/tree: `7a9d7d1f242fe46db1f258737305369f50ec69de` /
  `76aae2d9a42622db2984c86f3b3fa667f21c8f5d`.
- Branch: `codex/v013-evidence-provenance`; PR `#22` remains Draft.
- Package version: `0.12.0`; no tag, signature, RC, GA or registry publication.
- No repository `.env`, prior DeepSeek key, `~/.codex/auth.json`, Desktop login state or real
  Provider was read or used.
- A qualification wheel was not frozen because Human Gold/product-outcome gates have not passed;
  `wheel_sha256=not_executed` is intentional.

The detailed failure/root-cause/repair inventory is in
`docs/V0_13_PASS5_ROOT_CAUSE_LEDGER.md`.

## Qualification infrastructure closure

### Platform inventory

The historical `platform-core-test-manifest-v1.json` remains immutable at 1,339 common cases.
Pass 5 first reproduced the two known unexpected tests (1,341 current cases), then added explicit
regressions, bringing the current common collection to 1,370. The new preflight binds sorted IDs,
count, digest, selection, candidate HEAD/tree and clean/dirty state. Candidate mode records drift
and always keeps `release_ready=false`; manual Platform Core writes the receipt before exiting
non-zero on dirty state or collection drift.

This separates two meanings:

- current-source Candidate CI: ordinary regression evidence, claim-ineligible;
- manual Platform Core: exact frozen inventory, nine OS/Python cells, no unclassified skip.

The frozen inventory was not rotated because the final candidate is not frozen and product gates
have not passed. Platform Core is therefore `not_executed`, not passed.

### Document and provenance integrity

The PRD disposition sidecar now matches the actual document SHA-256
`003a3361713a86469af6fe787a61cb81b6d6d0db5f1c7f7cc583eb63d81789e4`, with mutation and
filename negative tests. Pass 4's Windows limitation was corrected to the already established
closed-environment root cause without rewriting the historical raw report.

The candidate-only provenance v1 envelope now enforces reference closure, frozen inputs, finite
metrics, derived run/dimension summaries, validator source/executable byte bindings, and 64 MiB
file bounds. It remains an envelope verifier only. It does not execute commands, validate a Host,
run a scorer, derive pass, or enable assembly. The product-outcome package v1 likewise rejects all
`passed` outcomes and exists only to let an Owner prepare isolated continuity/Wiki/legal inputs.

## Product-outcome evidence

### Task Continuity

Repository-visible development regressions passed for current checkpoint selection, superseded
revision rejection, distractor rejection, bounded Context, read-only behavior and deterministic
scoring. Those fixtures are candidate-visible and do not include the required three-way equal-budget
Host-only / Host-native Memory / Host-native Memory + DeepLaw experiment. An older visible
development density result remains below its frozen target (`RelevantChars/ContextChars=0.760628`
versus `0.8`).

Result: kernel regressions pass; `product_outcome_qualified=false` and
`human_gold_qualified=not_executed`.

### Human/Agent Wiki

Public seams now execute the three formerly skipped development cases:

- same title + distinct semantic keys remain two Knowledge IDs/pages and identity lookup is
  ambiguous;
- a shared admitted alias remains two candidates in the owner-visible Resolver and never becomes
  a silent merge;
- a typed three-edge cycle survives canonical relation revisions, rebuild, bounded graph reads,
  Registry/Link Index projection and verification;
- user-owned Markdown bytes are preserved.

The existing development Source → Statement/Relation → Wiki → exact Source/Locator chain also
passes with its known deferred Statement semantic-target Resolver. No independent human performed
the frozen page/backlink/outlink/disambiguation/evidence drill-down task.

Result: public-seam development behavior executed; `wiki_human_task_qualified=not_executed`.

### Protected/legal evidence

The existing unsigned synthetic development suite passes exact quote/locator/receipt tamper
rejection, wrong-version exclusion, no-answer Gap, Authority partition and
`origin=agent_derived` / `legal_authority=false` checks. It is not the exact signed or equivalently
verified 28-source Pack and is not independent legal Human Gold.

Result: development hard-zero regression only; `legal_pack_qualified=not_executed`.

## Query v6 development challenge

After the first Pass 5 commits, current-head Semantic workflow run `31341879530` exposed a
development regression that the focused tests had not represented. Its raw report at commit
`506f204bd32a0bc50874d3fb2044832561ea0584` (SHA-256
`05063ec3657bed1c9cf448132e8769fd05f3b839f07bc8fe097f9212e15035bd`) recorded 11/15 cases,
variant pass rate `0.714286`, Context accuracy `0.933333`, and one stale prohibited selection.
Sol independently reproduced the same result through the public CLI benchmark.

The correction does not change thresholds or budgets. It keeps the raw multilingual query and its
bounded expansion as separate reranker views and fuses each candidate's highest score from the
unchanged single-view reranker. A relevance-floor bypass is limited to full-word canonical
title/semantic-key/admitted-alias matches. A repeated independent proper-name anchor is retained
alongside a compound anchor so a multi-target request is not silently reduced to one target.

The same local CLI benchmark then returned 15/15 cases, 14/14 variants, Context accuracy `1.0`,
stale prohibited selections `0`, and Provider hard-limit violations `0`; the corrected raw report
SHA-256 is `1cb12753e12bdca21546f0327eb3ecdec2ad32315016a6e204f8a83520be75cc`.
Fresh current-head GitHub evidence is still required before handoff.

The first complete post-fix suite then exposed a separate fail-closed boundary: an all-uppercase,
delimiter-separated opaque identifier was being split into independent singleton identity anchors,
so its generic `fact` segment could bypass the unchanged v5 relevance floor. Opaque tokens are now
kept as one complete bounded anchor. The current repository-visible development v3 fixture rotated
only the byte hashes of the two changed indexed source files; historical v1, cases, labels,
expected IDs, thresholds and governance fields were not changed.

The initially fresh repository challenge was consumed while repairing this candidate and is now
explicitly `development_tuning_used`; it cannot be reused as qualification/final blind evidence.
Its public Python + MCP Context result was:

| Metric | Observed |
|---|---:|
| Useful Context Recall | `1.0` |
| False Suppression count | `0` |
| Wrong-target admission count | `0` |
| Distractor-induced wrong-target delta | `0` |
| RelevantChars / ContextChars | `469 / 469 = 1.0` |
| Redundancy count | `0` |
| Provider bytes | `8123` |
| Alias-collision candidates retained | `2` |
| Homonym candidates retained | `2` |

Expansion receipt bindings from that run:

- profile SHA-256: `7bdbb82c4de2a01a60a22c471080cd89703186db0c8bf45c232e59a7155740de`;
- lexicon SHA-256: `0d801ca669bdd9dfa613de31cb73a0b576947130623dc7162ee7af6d49fcbf21`;
- configuration SHA-256: `eea17154d0ad6e844c1e9fd14d1e10c145e434c337846d230b598c9635363991`.

The canonical visible semantic suite is again 15/15 with 14/14 query variants and no Provider
hard-bound regression in the local raw rerun above. These are development results only.

## Statement/Relation/Graph scale

The 5,001-Statement lane now uses 21 public semantic compilation runs over 21 immutable Source
Revisions and 21 Knowledge Revisions. The transient development report
`cb8bd8a3a5e7c5b28971f19cc5706a6f633cf980c628a7fbe3358ed1d18ea098` recorded:

- targets at positions `0`, `2500`, `5000` all selected;
- `tail_recall=true`, `position_independent=true`;
- Statement candidate bound `512`, maximum Provider bytes `7058`;
- one startup full verification and no per-query full verification;
- Source Revision inventory digest
  `d164170c8076525b2bf54f788415e4ece618e07c8b671fde90732228dd0516ab`;
- compilation-run inventory digest
  `1a15081f55ddb6ceffb25a410545643f4fa399d572a9203189427d690c01707e`;
- Knowledge Revision inventory digest
  `fabed52369c6d409f27c987a8cd4631a0205781bf3eb7fdaccd32d20a4715ee7`.

The 101 smoke graph report
`347c5177372104f50bb70392ac91086c22d406ed4b13b50de2d4659e297be849` executed nine typed
Relation Revisions and passed tail, hub, deep-chain, cycle, contradiction, temporal, dangling,
self-loop and `graph_hops=0/1/2` checks. Its `limit=1` probe also reproduced the unresolved
correctness blocker: selected count reaches the requested bound but no selection-truncation
flag/Gap/Receipt is emitted.

The report hashes above bind transient local development diagnostics, not committed release
artifacts. Statement 10k/100k, Relation 500/5000/10k/100k, Wiki 1k/10k/100k, 10,000-request RSS,
8-reader concurrency and cache-invalidation Core lanes were not executed. `scale_qualified=false`.

## Performance and artifact gates

The frozen Living Wiki comparator was not rerun because independent product outcomes did not pass.
Its retained Pass 4 development failures remain blockers: two repetitions showed cold/warm/query
regressions and rebuild regression up to `+116.1%`; thresholds were not raised and integrity checks
were not removed. `performance_qualified=false`.

The ordinary reproducible-build regression ran as part of pytest. That regression is not a frozen
candidate wheel, SBOM/provenance qualification, signed artifact, public redownload, or Platform
Core result. Those release artifacts remain `not_executed`.

## Owner prerequisites and execution order

Before independent qualification, Owner must provide outside the repository:

1. a frozen qualification corpus and Human Gold created without candidate-output access;
2. a fresh final-blind corpus/Gold replacement policy;
3. the signed or equivalently verified exact legal Pack and trust identity;
4. an owner-only isolated Codex evaluation identity/project/credential;
5. real Ubuntu/macOS/Windows runners for the final nine-cell Platform Core;
6. a public-redownload environment;
7. only if cross-host support is claimed: proof the old DeepSeek key was revoked and a new
   evaluation-only read-only secret file outside the repository.

Then execute: qualification holdout → candidate freeze → fresh final blind → real Codex ×3 →
exact legal Gate → required performance/scale → nine-cell Platform Core → reproducible supply
chain/public redownload → Owner release decision. Model output is never Gold.

## Local verification record

Commands used during Pass 5 include:

```text
uv lock --check
PYTHONDONTWRITEBYTECODE=1 uv run --frozen pytest --strict-markers -p no:cacheprovider -rs
uv run --frozen ruff check .
git diff --check

PYTHONDONTWRITEBYTECODE=1 uv run --frozen pytest --strict-markers -p no:cacheprovider -q tests/test_semantic_gold.py
PYTHONDONTWRITEBYTECODE=1 uv run --frozen pytest --strict-markers -p no:cacheprovider -q tests/test_v013_continuity_benchmark.py tests/test_v013_evidence_wiki_benchmark.py tests/test_v013_legal_exact_evidence_benchmark.py
PYTHONDONTWRITEBYTECODE=1 uv run --frozen pytest --strict-markers -p no:cacheprovider -q tests/test_v013_query_graph_scale.py tests/test_v013_query_graph_p0_reproductions.py
uv run --frozen python -m benchmarks.release.platform_inventory --mode candidate --selection common --output RECEIPT.json
uv run --frozen python -m benchmarks.release.platform_inventory --mode platform_core --selection common --require-match --output RECEIPT.json
uv run --frozen python -m benchmarks.v013.product_outcome_package --dry-run
uv run --frozen python -m benchmarks.v013.query_graph_scale --output REPORT.json --scale 5001
```

The final pre-commit complete run returned `1375 passed, 6 skipped in 371.26s`. The six skips are
not release passes: one unavailable historical v0.6 wheel, Statement 10k and 100k, Relation
500/5000, and two native Windows ACL/junction lanes. Current-head CI identity and final commit/tree
are reported in the task handoff after the documentation commit; this tracked document
intentionally does not claim a self-referential commit hash.

## Gate status

| Status | Value |
|---|---|
| `source_candidate_stable` | `true` locally; current-head Candidate CI required before handoff |
| `product_outcome_qualified` | `false` |
| `human_gold_qualified` | `not_executed` |
| `wiki_human_task_qualified` | `not_executed` |
| `legal_pack_qualified` | `not_executed` |
| `real_codex_qualified` | `not_executed` |
| `cross_host_qualified` | `not_executed` |
| `performance_qualified` | `false` |
| `scale_qualified` | `false` |
| `platform_core_qualified` | `not_executed` |
| `artifact_redownload_qualified` | `not_executed` |
| `release_ready` | `false` |
| `competitive_claim_eligible` | `false` |

Known limitations are the unresolved graph truncation receipt, external outcome/Host/Legal inputs,
the frozen Wiki performance failure, unexecuted scale/RSS/concurrency/cache lanes, unrotated
Platform Core inventory, missing historical v0.6 compatibility wheel, and absent public-redownload
evidence.

Final release disposition: `source_candidate_remains_not_released`.
