# DeepLaw v0.13 Pass 8 capability gap matrix

Status: **development evidence and construction scope only; not qualification or release evidence**.

## Freeze and method

- Frozen input branch / commit / tree: `codex/v013-evidence-provenance` /
  `cae4bdf2a91e1a2cf828fa7c6e7b081313632bba` /
  `fb2e4d104af0917c2aedc4b77f4ac89f4c55b6db`.
- Pass 8 branch: `codex/v013-pass8-lean-qualification`.
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
| 15 | 10k normal use; 100k bounded | 10k Link Index sharding exists; full Wiki/Relation/RSS lanes are not executed | `partial` | Execute frozen 5k/10k/100k candidate lanes once after correctness freeze |
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
`86d35bae51d350c1e3bdfc0c2edffdad38794347afad6dc1976a37a43f839ade`. The frozen
Gold is still `machine_review_pending`, so every result below is Development evidence with
`qualification_eligible=false`.

| Evidence | Result |
|---|---|
| deterministic semantic lifecycle v2 | `passed`; report `semanticdeterministic_66a5758029a78d19c7ff0fbc`; file SHA-256 `d6614e874ab39a9f133cb6b7b6b84931b97ae65253a17368b463429d7afb70b1` |
| semantic query diagnostic | `passed` 15/15 canonical and 14/14 variants; report `semanticqueryrun_00c30abb2cfa3872239f1e99`; file SHA-256 `dd435b90e0d14b277773de001772c5314ced70cbece3421ba8b2387cee89f82f` |
| Context v2 | `passed` 15/15; report `semanticcontextoutcome_bd7c0a255c43cb5d5ea76d6f`; file SHA-256 `8c20e2f3c09015b083e892b2662e48a6c8beecadb9a245e4cce653e040c8117c` |
| query cost v2 | file SHA-256 `3efa4f1f034a2b1f6b411fab92e3366770e14a471971f5ce6393874399c8adba`; actual Provider input tokens remain `null` |

Corrected provider-visible metrics are Precision@K `0.585238`, Recall@K `1.0`, MRR
`0.855556`, and nDCG@K `0.890684`; target-scoped Precision@K remains a separate diagnostic at
`1.0`. False Suppression is `0.0` with zero suppressed, undiscovered, rejected, uncompiled, gap, or
otherwise missed required targets. Duplicate Evidence is `0.0`. Exact surface identity parity,
request-parameter parity, receipt explain, and provider hard-limit failures are all zero. Local
Capsules total `340037` UTF-8 bytes, provider Capsules `95523`, provider content `90976`, MCP tool
results `99888`, and canonical transport-envelope metadata `8912`; `24977` is explicitly a
UTF-8-bytes/4 estimate, not measured Provider tokens. Token savings and the equal-budget distractor
delta are honestly `not_executed` because no frozen comparator pair exists.

The exact Tolaria external source probe is report
`tolaria_interop_032e0d74e03671a6a03e9ab7`, self-addressed report SHA-256
`ead081dee556b1fa4f71b432de7f69d69d5b1ae7a49cbe93888ecf61eca1fd09` (serialized file SHA-256
`87cd31b32f9eff1910a20e353e5f84d5c98a8166b1a462d085ced6c65dba1727`). It verifies the
exact Tolaria commit and file hashes, allowed-note read/open/`expectedMtime` update, protected-target
policy denial, and unchanged protected hashes. `expectedMtime` is explicitly not a DeepLaw
Revision; no canonical Ledger write occurred. The external dependency audit has six known high
findings, none redistributed by DeepLaw. OS sandbox proof, desktop GUI behavior, reconciliation,
and release-time contributor/file rights confirmation remain open.
