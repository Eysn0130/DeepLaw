# Named upstream product closure, 2026-08-18

Status: **research and development evidence only**. This document is not a release ledger,
qualification receipt, Human Gold, legal attestation, or product-status authority. Current machine
state remains in the active qualification records. Package/main remain `0.12.0`,
`release_ready=false`, and `claim_eligible=false`.

This review compares user outcomes at exact upstream commits without changing DeepLaw's frozen
architecture: one Shared Governance Kernel, the three product roles Task Continuity / Governed
Project Knowledge, Source-native Evidence Library, and Living Wiki, and one shared Context
Compiler. No upstream path, title, link, score, page, or generated summary may replace DeepLaw
identity, Source Revision, Ledger state, Authority, scope, sensitivity, lifecycle, or admission.

## Coordinates and observation boundary

`qualification coordinate` and `research anchor` are intentionally different columns. A moving
branch is an observation only, even when it happens to equal the exact research anchor on the
observation date.

| Upstream | Frozen v0.13 qualification coordinate | Exact research anchor | Commit time / subject | Observed moving branch |
| --- | --- | --- | --- | --- |
| [OpenWiki](https://github.com/langchain-ai/openwiki) | released v0.3.1, peeled `630eb9ec3fa22a4bed2d347fc3ea3a6a3bd22abc` | [`21746ce996f3a69898883da58b122770f7dbd668`](https://github.com/langchain-ai/openwiki/tree/21746ce996f3a69898883da58b122770f7dbd668) | 2026-08-17T23:04:17-07:00; `feat: configure model output and bedrock stream limits (#459)` | `main` at the research anchor, observed 2026-08-18 15:40 +08:00 |
| [Tolaria](https://github.com/refactoringhq/tolaria) | `v2026-08-11`, `cb45f26649a7500e0bdb5dd0b8f0412e9c1daf4d` | [`40cc9f9479fef7bfe8a51a6df7e02fe11971f95e`](https://github.com/refactoringhq/tolaria/tree/40cc9f9479fef7bfe8a51a6df7e02fe11971f95e) | 2026-08-17T16:09:39Z; `fix: preserve selected text when pasting links` | `main` at the research anchor, observed 2026-08-18 15:40 +08:00 |
| [Obsidian API](https://github.com/obsidianmd/obsidian-api) | `obsidian@1.13.2`, `cc1744324150c632416857c98964f87b1574a5fc` | [`cc1744324150c632416857c98964f87b1574a5fc`](https://github.com/obsidianmd/obsidian-api/tree/cc1744324150c632416857c98964f87b1574a5fc) | 2026-07-14T14:31:55Z; package/API definitions for Obsidian 1.13.2 | `master` at the same commit, observed 2026-08-18 15:40 +08:00 |
| [Ekgardt/llm-wiki](https://github.com/Ekgardt/llm-wiki) | none; the protocol retains an unnamed LLM-Wiki behavior category | [`350eec8a284e159b2e4cfd068d808cbf203a6cc5`](https://github.com/Ekgardt/llm-wiki/tree/350eec8a284e159b2e4cfd068d808cbf203a6cc5) | 2026-08-17T07:17:09-03:00; `feat: close security and reliability audit gaps` | `main` at the research anchor, observed 2026-08-18 15:40 +08:00 |

OpenWiki, Obsidian API, and Ekgardt/llm-wiki carry MIT licenses at the exact research anchors.
Tolaria carries AGPL-3.0-or-later. This change uses documentation, code, and tests as reference
evidence only; it copies no upstream implementation and adds no upstream dependency. Obsidian API
is a type surface, not evidence of Obsidian Desktop behavior.

## Classification rules

- **Adopt**: retain the user outcome or guardrail as a DeepLaw development task using existing
  primitives. It does not authorize copying the implementation.
- **Already stronger**: current DeepLaw contracts are stricter in the relevant trust dimension;
  the named public task must still execute before the label is treated as observed behavior.
- **Defer**: potentially useful, but not required by the frozen v0.13 Kernel or not evidenced by a
  current task.
- **Reject**: conflicts with the frozen product, Authority, privacy, or mutation boundary.
- **Unverified**: the exact upstream material or current DeepLaw development run does not support a
  conclusion.

## Named behavior matrix

| User outcome | Exact upstream evidence | DeepLaw disposition | Public DeepLaw task and evidence boundary |
| --- | --- | --- | --- |
| 1. Install, doctor, time to first useful Wiki | OpenWiki documents `npm install -g openwiki` and `--init` but no dedicated doctor. Ekgardt/llm-wiki has exact-commit bootstrap and a doctor/repair contract. Tolaria documents install and a Getting Started vault. None publishes a measured first-use time at these anchors. | **Adopt** the explicit readiness and elapsed-time measurement; **Unverified** for cross-product speed. | `deeplaw init`, `deeplaw doctor`, Source add, compile/project, then Wiki browse. Record elapsed wall time and actionable Gaps; do not compare unmeasured upstream time. |
| 2. Full, incremental, and no-op update | OpenWiki hashes source snapshots and skips a clean unchanged update. Ekgardt/llm-wiki separates full/changed compilation and skips without pending work. Tolaria uses full cache rebuild, Git delta, and same-HEAD no-op behavior. | **Already stronger** if exact inputs produce equivalent manifests and an unchanged projection performs no rewrite. | Public compile/reconcile and Wiki files; compare manifest/content hashes and owned-file mtimes. Historical or private projector helpers are not task evidence. |
| 3. Recovery after interruption | OpenWiki records interrupted state and prevents a false no-op. Ekgardt/llm-wiki has transaction abort/generation recovery. | **Adopt** the named crash/recovery outcome; DeepLaw's Compilation Run and pending materialization design is stricter but current execution remains required. | Interrupt before canonical commit and after commit/before projection only through the public Compilation Run lifecycle. If the development diagnostic does not inject an interruption, report `not_executed`. |
| 4. User file and user-owned block protection | OpenWiki writes only owned markers/paths. Ekgardt/llm-wiki uses before/after hashes and does not overwrite user configuration. Tolaria is files-first but has no DeepLaw ownership manifest. | **Already stronger** for file ownership; **Unverified** for arbitrary user-owned block preservation. | Create an unowned Wiki file, reconcile/reproject, and compare exact bytes. Do not infer block-level ownership from editor rendering. |
| 5. Alias, same-name entity, rename, and move | Tolaria resolves aliases and path-suffix disambiguation but a bare same-name link may use the first match. Ekgardt/llm-wiki emits `ambiguous_target`; its source identity remains path-bound. Obsidian API exposes rename/link helpers only. | **Already stronger** for stable identity and ambiguity failure; **Adopt** file-operation ergonomics only. | Rename/move governed Markdown and reconcile through the grant. Same-name or alias collision must stay ambiguous or fail validation; no path/title may become identity. |
| 6. External edit and reconcile | Tolaria has a native watcher, debouncing, application-write suppression, and clean-buffer refresh; unsaved buffers remain editor-owned. Ekgardt/llm-wiki uses hash-bound replace transactions. | **Already stronger** for governed revision/CAS reconciliation; **Defer** desktop unsaved-buffer behavior. | Edit the current governed Markdown file, run `knowledge reconcile`, verify a new revision and unchanged Authority. Desktop buffer/UI behavior remains `not_executed`. |
| 7. Backlink/outlink, source successor, wrong merge | Tolaria and Obsidian expose navigation links but not Source Revision successor semantics. Ekgardt/llm-wiki lints missing backlinks and quarantines ambiguous/conflicting targets; page-level supersession is not immutable source lineage. | **Already stronger** for hash-bound Link Index, source successor, and wrong-merge failure. | Public Wiki backlinks/outlinks plus Source add/update/diff/fragment. A plain Wikilink remains navigation only. Missing public execution stays `not_executed`, not inherited from unit tests. |
| 8. New thread, ordinary resume, fork, compaction | These Wiki/editor projects do not provide a comparable governed Host task-lineage contract. | **Already stronger** as a product primitive, subject to local public-seam execution; named upstream comparison is **Unverified**. | `task start/locate/checkpoint/resume/fork/compaction`; no transcript or Host memory import. Native real-Host behavior is separate qualification input. |
| 9. Concurrent worktree, stale checkpoint, wrong task | OpenWiki taskflow fixtures cover stale/renamed knowledge traps, not governed worktree admission. The other anchors do not establish a comparable route/snapshot contract. | **Already stronger** if Wrong-State Admission remains zero in the development task. | Use distinct Git worktrees and task handles, change the workspace after a checkpoint, and verify structured Gap plus no selected stale checkpoint. |
| 10. Selective forget | No exact anchor establishes DeepLaw-equivalent scope-aware checkpoint forgetting with retained audit identity. | **Already stronger** if forgotten content is withheld and unrelated knowledge remains; upstream comparison is **Unverified**. | Explicit granted `task forget`/`knowledge forget`, then resume/context. This is a durable mutation; reads before and after remain no-write. |
| 11. Original PDF, DOCX, HTML, and Markdown bytes | OpenWiki has Markdown/OKF surfaces but no exact-anchor multi-format evidence contract. Ekgardt/llm-wiki collects Markdown and text/code rather than native PDF/DOCX/HTML evidence. Obsidian/Tolaria are Markdown work surfaces. | **Already stronger** for Source-native Evidence Library, subject to exact-byte public receipts. | Add local synthetic files with model/OCR sidecars disabled, compare caller SHA-256 to the Source receipt, run doctor, and preserve honest extraction Gaps. No formal Legal Pack claim follows. |
| 12. Version, Fragment, Locator, exact quote, effective date | None of the named Wiki/editor anchors establishes the full exact-byte/version/locator/valid-time chain. | **Already stronger** for identity and drill-down; effective-date correctness is **Unverified** until a source-specific task runs. | `source get/fragment/verify`, quote/evidence-first Context, exact source hashes and locator. A source-only or insufficient duty must remain a Gap. |
| 13. Exception, proviso, cross-reference, wrong version, false Authority, Gap | The anchors provide link or citation checks but not DeepLaw's separated Authority/admission duties. | **Already stronger** in contract design; **Unverified** for any duty not executed in the development fixture. | Freeze applicable duties, withhold incomplete exact evidence, and verify false Authority/wrong-version admission remains zero. Signed Legal Pack tasks stay outside this Goal. |
| 14. Provider include/exclude, duplicate, distractor, and budgets | Ekgardt/llm-wiki has bounded context packing and fail-closed mandatory items. OpenWiki exposes provider output controls, not a unified evidence-admission budget. | **Adopt** complete-item packing as a guardrail; **Already stronger** for admission, source/item/character/token/hop and 65,536-byte output bounds. | Real `knowledge_support` stdio `tools/list` and `tools/call`; current advertisement must be only query/context/explain. Record actual content bytes. Native Host token usage is unavailable without a supported real-Host run. |
| 15. Daily 1k/10k Wiki operations | Tolaria describes a 9k/10k-note motivation. Ekgardt/llm-wiki contains a scale harness and 10k file bound. Neither exact anchor provides retained 1k/10k daily-operation results; OpenWiki has no matching report. | **Unverified** across products. DeepLaw becomes observed development evidence only after the public CLI scale run. | Build exact 1k and 10k fixtures through CLI Compilation Packets, then browse/query/reconcile through public seams; record elapsed time, peak RSS, storage, file counts, and failures. This is not platform or release qualification. |

## Development task contract

The retained runner is `benchmarks/v013/run_upstream_product_closure.py`. It may use only the
first-party CLI executable, the current `knowledge_support` stdio MCP advertisement, Living Wiki
files/reconcile, and Task Continuity/Host enrollment. It must not import DeepLaw domain helpers,
write SQLite directly, read a Host credential, call a real model, or author a formal Gate result.

The report must record:

- exact DeepLaw commit/tree/package and `evidence_class=development_diagnostic`;
- user steps, First Correct Action, Decision Preservation, Wrong-State Admission, equivalence,
  user-file protection, exact Source drill-down, Gap correctness, and failures;
- actual Provider UTF-8 content bytes and `tokens=unavailable` when no supported native usage is
  observed;
- elapsed time, peak RSS, storage, and the exact requested scale; and
- explicit `not_executed` for real Codex/OpenCode, desktop UI, signed Legal Pack, 3 OS, supply
  chain, Human Gold, and isolated formal scoring.

The committed development report path and exact measured results are added after the runner passes
its public-seam regression and the 1k/10k executions finish.

## Scope decision

The named research found useful update, recovery, file-protection, ambiguity, and bounded-context
guardrails. Every one fits existing Source Revision, Knowledge Revision, Ledger, Wiki,
reconciliation, Context, receipt, and Task Continuity primitives. No observed task justifies a new
database, Knowledge kind, Relation predicate, page family, Host adapter, Agent runtime, connector,
telemetry path, GUI, second retrieval engine, second Ledger, or second Authority model.

Real Host/model calls remain `not_executed` in this Goal unless the owner already has a supported
authentication seam that needs no credential inspection or copying. Their absence does not block
this development comparison and cannot be rewritten as product evidence.

