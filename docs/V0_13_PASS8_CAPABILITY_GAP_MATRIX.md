# DeepLaw v0.13 Pass 8 capability gap matrix

Status: **development evidence and construction scope only; not qualification or release evidence**.

## Freeze and method

- Frozen input branch / commit / tree: `codex/v013-evidence-provenance` /
  `cae4bdf2a91e1a2cf828fa7c6e7b081313632bba` /
  `fb2e4d104af0917c2aedc4b77f4ac89f4c55b6db`.
- Pass 8 branch: `codex/v013-pass8-lean-qualification`.
- Final code candidate commit / tree: `2a635d228e99537304282223ae08ef066a4961e2` /
  `566b2f546816264d997f1418c61be6c25cdb2494`.
- One-time rule freeze commit / tree: `4befa479a063e2c022814d8d9f15feeeecbee5b9` /
  `7e8602eb9ecaee6f16044d3786d8623ea3cb50ab`.
- Frozen `AGENTS.md` SHA-256:
  `e8d14f80295a4e923b72b51a54b6d189d953a640e5771f8d2577d036c5296514`.
- OpenWiki: `langchain-ai/openwiki@7531d615216e8cbccf464f66cfbbae3668871c84`,
  version `0.3.1`, MIT.
- Tolaria: `refactoringhq/tolaria@ab01faa6773136a58285d04cb81e2587c11bac85`,
  published AGPL-3.0-or-later, with the Owner-declared same-team authorization and release-time
  file/contributor-rights condition recorded in `UPSTREAM_REUSE.md`.

`pass` below means a focused local behavior has executable development evidence. It never means
Human Gold, real Host, cross-OS, commercial qualification, or release readiness. `partial` and
`fail` are the only construction inputs. Upstream features outside DeepLaw's product definition are
`not-product`, not gaps.

## OpenWiki / Tolaria capability triage

| Upstream capability | DeepLaw existing behavior | Public task | Baseline status | Minimum action | Reuse candidate | Focused validation |
|---|---|---|---|---|---|---|
| OpenWiki internal-link validator | Registry-declared Wikilinks have resolved/ambiguous/unresolved edge state, exact backlink/outlink counts, and no filesystem scan on read | Find broken Wiki navigation without changing identity or evidence | `pass` for Wikilinks; inline Markdown heading anchors are `not-product` | Keep current indexed status; do not add recursive scans or broken-link stamps | `reference` only: `src/agent/wiki-link-validator.ts` | `test_v013_wiki_link_index.py` |
| OpenWiki content snapshot/update no-op | Projection change sets and content hashes skip unchanged writes | Rebuild an unchanged Wiki without rewriting files | `pass` | None | `reference` only: `src/agent/utils.ts`; Git scheduler is `not-product` | `test_v013_projection_incremental.py` |
| OpenWiki managed blocks and index sync | Canonical editable Markdown and rebuildable derived pages use different ownership; unregistered user files fail closed rather than being overwritten | Preserve user files and authored content | `pass`; CI/AGENTS injection is `not-product` | Keep ownership separation; do not import managed CI blocks or recursive `index.md` sync | `reference` only: `src/ingestion/code-mode.ts`, `src/okf/index-sync.ts` | `test_knowledge_markdown.py`, `test_v013_projection_ownership.py` |
| OpenWiki OKF frontmatter/labels | DeepLaw has a closed YAML/frontmatter contract, stable Ledger identity, and Unicode title/alias fields | Read plain Markdown/YAML and resolve human labels | `pass` for DeepLaw contract; OKF compatibility is `not-product` | None | `behavioral` fixtures only if an explicit OKF adapter is later approved | `test_source_adapters.py`, `test_autonomous_knowledge.py` |
| OpenWiki browser visualizer | CLI, Markdown and editor adapters already expose bounded navigation; graph JSON is Registry-derived | Browse governed knowledge | `not-product` for v0.13 core | Do not add a second viewer while CLI + Tolaria can satisfy the task | none | N/A |
| Tolaria Wikilink table/fence/alias/CJK fixtures | Link Index excludes code, preserves registered source bytes, resolves target identity, and Resolver keeps ambiguity explicit | Open/edit multilingual Markdown without corrupting navigation | `pass` after focused behavioral fixtures | Retain re-authored Python fixtures; no implementation copy | `behavioral`: `src/utils/wikilinks*.test.ts`, `tests/smoke/wikilink-traditional-chinese.spec.ts` | `test_v013_wiki_link_index.py`, `test_v013_wiki_resolver.py` |
| Tolaria external rename/cache/crash behavior | Stable ID is path-independent; reconciliation creates new revisions; projection journal recovery is atomic and tamper-evident | Rename/move/edit and recover without last-writer-wins | `partial` | Run an exact external Git move/cache-invalidation task; retain Revision/hash rather than mtime Authority | `reference`: ADRs 0036/0075/0077/0111/0135/0170 | `test_autonomous_knowledge.py`, `test_v013_projection_recovery.py` |
| Tolaria median/p90 and ratcheted thresholds | DeepLaw records p50/p95/p99 with frozen 1k/10k/100k lanes; expensive lanes are explicit | Keep editor and Wiki latency bounded | `partial` | Re-author finite-sample/warmup/ratchet tests if used; never relabel p90 as p95 or relax thresholds | `behavioral`: `scripts/editor-performance-*` | `test_v013_scale_performance.py` |
| Tolaria direct Workspace open/edit | Exact frozen Tolaria MCP tool-service opens, reads and edits an allowed Markdown note in an isolated synthetic Workspace; DeepLaw policy rejects protected roots before they are sent upstream | Tolaria opens a DeepLaw Workspace and edits only allowed Markdown | `partial` | Retain the exact source-level interop report; a real Tolaria desktop GUI open/edit plus DeepLaw reconciliation remains unexecuted | `reference` external execution only; no Tolaria runtime in DeepLaw Core | `test_v013_tolaria_workspace_interop.py` plus exact external report |
| Tolaria mixed read/write MCP and `expectedMtime` | DeepLaw uses separate read-only support and grant-bound sink processes with expected Revision/hash | Prevent hidden mutation and conflicts | DeepLaw boundary `pass`; upstream mixed process is `not-product` | Do not copy; translate only the conflict user task to Revision/hash semantics | `reference` only: `mcp-server/tool-service.js` tests | MCP annotations, sink conflict, and read-only tests |

No OpenWiki or Tolaria implementation is copied into DeepLaw by this matrix. Accepted sibling
reuse is independently re-authored behavioral fixtures, architectural reference, and execution of
Tolaria's exact external source checkout by a development-only interoperability probe. The external
AGPL checkout and its dependencies are not packaged or redistributed by DeepLaw.

## Minimum Wiki parity outcomes

| # | Outcome | DeepLaw evidence | Baseline status | Minimum action / release boundary |
|---:|---|---|---|---|
| 1 | Plain Markdown/YAML is human- and Agent-readable | Markdown Source IR and closed frontmatter tests | `pass` | None |
| 2 | User files and user-authored content are not overwritten | untracked user-note, projection ownership, and no-op tests | `pass` | None |
| 3 | Source-grounded init/update | immutable Source Revision, compilation and successor tests | `pass` | None |
| 4 | No-change no-op | unchanged projection hashes do not rewrite live pages | `pass` | None |
| 5 | Exact title, ID, alias and multilingual alias | explicit Resolver channels plus multilingual exact/ambiguous alias fixture | `pass` | Independent identity Gold remains required for qualification |
| 6 | Ambiguity never becomes a wrong merge | Resolver limit preserves ambiguity; compiler identity candidates fail closed | `pass` | Independent wrong-merge Gold remains required |
| 7 | Wikilink, backlink and outlink | Link Index edge identity, counts and cursor tests | `pass` | None |
| 8 | Typed Relation is separate from navigation link | Link edges bind page revisions; governed Relations use separate canonical revisions | `pass` | None |
| 9 | Source Revision/fragment/locator drill-down | Source page/fragment anchor Resolver and exact evidence task | `pass` | Human Gold remains required |
| 10 | Rename/move preserves stable identity | workspace move/reconcile test retains Knowledge ID and revision history | `pass` | None |
| 11 | External edit reconciliation | external edit, stale base, preserved conflict and CRLF tests | `pass` | Exact Tolaria edit remains outcome 16 |
| 12 | Broken/orphan/dangling/self-loop/cycle are explicit | zero-link coverage, unresolved edges, graph dangling/self-loop/cycle fixtures | `pass` at smoke scale | Large Relation/Wiki lane remains outcome 15 |
| 13 | Incremental/full rebuild equivalence | deterministic change set, recovery and v3 integration tests; a real 1k source successor produces exact incremental/full v2/v3, Registry, Link Index and Resolver equality | `pass` at 1k | Re-run the affected frozen candidate lanes once; do not repeat 100k during ordinary fixes |
| 14 | Git/history/recovery | immutable revision history and crash recovery pass; exact external Git rename task absent | `partial` | Run one external Git move/cache invalidation task; do not make Git the Authority |
| 15 | 10k normal use; 100k bounded | 5,001/10k/100k Statement tail retrieval is bounded; the 10k-request RSS/concurrency lane and 1k exact rebuild/cache lane executed; large Wiki and Relation lanes did not | `partial` | Retain the executed development evidence; run audited 5k/10k/100k Relation and 10k/100k Wiki lanes without private construction before release |
| 16 | Tolaria can open the DeepLaw Workspace | exact frozen Tolaria source-level MCP service opens/reads/edits the allowed note; protected roots remain outside the upstream call | `partial` | Real Tolaria desktop GUI open/edit and post-edit DeepLaw reconciliation are `not_executed`; do not call the source-level probe a desktop E2E |
| 17 | Agent receives bounded Context through CLI/MCP | corrected v6 development suite passes 15/15 cases and exact Local → Provider → MCP identity/request parity | `partial` | Real Host ×3 and actual Provider token usage remain release gates |
| 18 | Protected evidence cannot be rewritten by editor or Agent | read-only roots, immutable Sources, separate sink grant and protected-root tests; exact Tolaria probe denies every protected target and verifies unchanged hashes | `pass` locally | OS sandbox isolation and real desktop behavior remain release evidence gaps |

## Context A–G baseline reproduction

The exact-wheel Development run used wheel SHA-256
`5c53e102a7cdbec50abee9b8b97b883b385d5496c2962f599858b172eca16a3e` and produced a
`tuning_used_development`, `qualification_eligible=false` Context v2 report. It passed 15/15
canonical and 14/14 variant cases, but that headline did not make the measurement definitions
correct.

| ID | Finding | Baseline status | Root cause | Minimum correction |
|---|---|---|---|---|
| A | Query Trace reason taxonomy | `fail` | unknown reasons/duties were hashed, not rejected; schema accepted arbitrary strings | closed runtime/schema enums; canonical producer codes; unknown drift fails closed |
| B | local / Provider / MCP measurement boundary | `pass` with naming caveat | five byte boundaries are separate, but `transport_metadata_bytes` is a canonical JSON envelope difference | document the exact method; do not call it network-wire metadata |
| C | local → Provider → MCP ID/semantic parity | `partial` | shared assembler exists, but tests and harness did not compare exact selected Statement/Revision/Source/gap sets | add exact three-surface identity and semantic parity |
| D | v6 Precision@K, Recall@K, MRR and nDCG | `partial` | metrics existed only in the historical v5 operator diagnostic, not primary Context v2 | add ordinary provider-visible per-case/aggregate Precision@K, Recall@K, MRR and nDCG; retain target-scoped precision only as a separate diagnostic |
| E | False Suppression | `fail` | defined as `1 - Useful Context Recall`, conflating not discovered, rejected, absent and suppressed | count only required Gold targets tied to a discovered candidate that selection suppresses; report other misses separately |
| F | Duplicate Evidence | `partial` | harness merged Statement citations with evidence and used a coarser identity than selection | deduplicate actual evidence items by canonical evidence/full source-fragment identity; keep channel overlap separate |
| G | bytes versus Provider tokens | `pass` for v2 development boundary | UTF-8 bytes, char counts, byte-derived estimate and nullable actual usage are distinct; actual usage is absent | keep actual tokens null until Provider usage; preserve `qualification_eligible=false` |

Baseline report SHA-256 values:

- semantic query diagnostic: `fe56ac10c1eb9dfc7107dfb339c30bbd1a9fefebcf3af387d0ac5856c78948ce`;
- Context v2: `1506cf237a3105e31ab73ab58bab03fd27aba50f241aeb009cd2933479447a7e`;
- query cost v2: `d5a9f02248e560194ffb4de813b284e45df525ea58435495d80cd0cc67eab53e`.

These files remain in the isolated temporary Development workspace and are not release artifacts.
At this freeze there is no repository-external qualification/final holdout or confirmed Human
Gold input, so those gates remain `not_executed`.

## Corrected Development outcomes

The minimum A–G corrections were rerun from the first-party `deeplaw knowledge context` command,
the `deeplaw knowledge query` audit projection, MCP `knowledge_context`, and receipt explain. The
isolated runtime installed only `deeplaw-0.12.0-py3-none-any.whl`, SHA-256
`48afb6e70a4ce8e8e2ce7e6d68b6cb1a9f58cb3a00f7ea401a19df0133bf4e82`, built from the final
code candidate. The affected runtime evidence is bound to commit/tree
`ea0a44c0b76f9ec23bb3482feea1bd621e0b1df7` /
`8e09bc6eb648a6ccbb2c1e2dfeb2addf577221c2`; the only later code-candidate change retired a stale
5,001-Relation diagnostic reason and did not change packaged runtime source. The frozen
Gold is still `machine_review_pending`, so every result below is Development evidence with
`qualification_eligible=false`.

| Evidence | Result |
|---|---|
| deterministic semantic lifecycle v2 | `passed`; report `semanticdeterministic_09c7006509aa34780f409a8b`; file SHA-256 `84253336455ccd2e467d5890e657eac8529f4a682d361e37e877158324cd2297` |
| semantic query diagnostic | `passed` 15/15 canonical and 14/14 variants; report `semanticqueryrun_aecff9f2fb97c6e5c5498e65`; file SHA-256 `47b4ed432ce05ee5bd842309f3640df54f40c18686c5b85099b5fee455754025` |
| Context v2 | `passed` 15/15; report `semanticcontextoutcome_2930d43da788befb7d83e7a7`; file SHA-256 `19dca907f6c94f9e89990dd1ecca13d78e9e9a4a8dded0ce5f2243921793534f` |
| query cost v2 | file SHA-256 `13234a16d1d834a87b543307bcb371759b54bc111c15f0d6ff474e3a2a94860d`; actual Provider input tokens remain `null` |

Corrected provider-visible metrics are Precision@K `0.581667`, Recall@K `1.0`, MRR
`0.855556`, and nDCG@K `0.890684`; target-scoped Precision@K remains a separate diagnostic at
`1.0`. False Suppression is `0.0` with zero suppressed, undiscovered, rejected, uncompiled, gap, or
otherwise missed required targets. Duplicate Evidence is `0.0`. Exact surface identity parity,
request-parameter parity, receipt explain, and provider hard-limit failures are all zero. Local
Capsules total `343473` UTF-8 bytes, provider Capsules `96585`, provider content `92038`, MCP tool
results `100950`, and canonical transport-envelope metadata `8912`; `25243` is explicitly a
UTF-8-bytes/4 estimate, not measured Provider tokens. Token savings and the equal-budget distractor
delta are honestly `not_executed` because no frozen comparator pair exists.

The baseline snapshot intentionally contained no `.deeplaw/derived` directory. The first attempt
therefore exposed a canonical-read defect: the live observer required a rebuildable projection
parent before it could return canonical state. The minimum fix treats an absent derived tree as the
explicit `("missing",)` identity while retaining safe-directory and symlink rejection. The
corrected exact-wheel run above passed directly from the no-derived snapshot without a manual
rebuild.

The first 100k Statement construction attempt then failed before retrieval because v3 semantic
inventory froze up to 10,000 globally admitted candidates and copied that local inventory into the
Provider-visible finalization packet. The minimum correction performs exact admitted lookup for at
most 256 observation-relevant identity keys, records truncation and the full local digest, and
projects only task-relevant canonical knowledge. A focused 10k-candidate regression keeps the
packet within 65,536 bytes, and the affected 100k public-profile construction/retrieval lane passed.

The exact Tolaria external source probe is report
`tolaria_interop_e9df12e58307d1da94ad995a`, self-addressed report SHA-256
`ce59e1fa905448c77ce59d938129abb66562de37bffb7383e1afe631caaf921a` (serialized file SHA-256
`8e0afc232059b6a9c320aeea1f7c6443eedcdbd19582b4b98e9c110354af7460`). It verifies the
exact Tolaria commit and file hashes, allowed-note read/open/`expectedMtime` update, protected-target
policy denial, and unchanged protected hashes. `expectedMtime` is explicitly not a DeepLaw
Revision; no canonical Ledger write occurred. The external dependency audit has six known high
findings, none redistributed by DeepLaw. OS sandbox proof, desktop GUI behavior, reconciliation,
and release-time contributor/file rights confirmation remain open.

The 1k construction scale report is bound to the earlier exact runtime commit and has file SHA-256
`ae96056ac2a5c34699bd7789f64db93cca5cba808f59c76a98abf0b4065a6b8f` and self-addressed
report SHA-256 `453886430bb09164dff6bc873b1401aadd2acafb200d6ab4254b369fed4c702f`.
It reports no failed or degraded operation: Wiki page p95 `76.392083 ms`, backlinks p95
`75.547916 ms`, compiled-first p95 `8.597625 ms`, exact projection equivalence, no stale cache,
eight successful concurrent readers, and zero provider hard-limit violations.

The dedicated Python 3.13 runtime-stability report executed 10,000/10,000 requests and 8/8
barrier-synchronized read-only readers with no failure and no canonical Ledger mutation. Current
RSS changed from `99,450,880` to `85,934,080` bytes (`-13.591433%`); peak RSS is unavailable under
the recorded macOS sampling method. Its file/self-addressed SHA-256 values are
`a2fbd98fa42960f8fc9efcad9643169a05d53de201d38da75884064c02e8c23a` /
`a7b6f328d1b1acbb6d79e51a166144e9aad9237836948967f9a806cd0b4fd5e3`.

The 5,001 and 10,000 exact final-code-candidate Statement reports have file SHA-256 values
`18d85ee7c90ae87b35d39984b2b5bd993c7ce4e22873079ece9d3e5bd5c26f11` and
`0977588d9fc41d2789325c9acd9b86fa2b3d13770ebd3224cc7aa59521067c76`. The affected 100k report
has file SHA-256 `4f280dff9f0f368fe4a4837d1178edfdd30ffeb3b722c1ec0c8a8d25846b102d`.
All sampled head/middle/tail Statements were selected, candidate discovery stayed bounded at 512,
and the largest Provider payload was `7,060` bytes against the `65,536`-byte hard limit. The 100k
fixture recorded 400 public compilation runs, 310,116 files, `1,188,124,770` storage bytes, and
process peak RSS `1,993,129,984` bytes. These are claim-ineligible construction diagnostics.

Relation/Graph 5,001/10k/100k and Wiki performance 10k/100k remain `not_executed`. The only public
Relation mutation seam is rate-bounded at 120/minute and no audited bulk constructor exists; the
runner did not substitute private SQL/Ledger writes. These unresolved lanes keep outcome 15
`partial` and block release qualification.
