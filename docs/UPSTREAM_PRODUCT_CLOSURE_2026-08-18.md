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

The retained report is
[`benchmarks/v013/upstream-product-closure-development-2026-08-18.json`](../benchmarks/v013/upstream-product-closure-development-2026-08-18.json),
SHA-256 `95544b918579c96741606d06e85e0910fbd4f62209e5ed94de57cac2a4fb1e11`.
It binds DeepLaw commit `fd3fd3d61bf7a2f3f5f59f994b4cc35347322f2b`, tree
`570347c783f5ae57f1b23a624f621a4a3b5c9bd1`, package `0.12.0`, and `uv.lock`
SHA-256 `e2cacd96e66132fcb28f1b9bf4746709ad2696159ffb8498ddf0769c213a7082`.
The runner observed a clean worktree before writing the report.

## Observed development results

The following is one-machine, synthetic, public-seam development evidence. It is not a named
upstream performance comparison and does not establish formal qualification.

| Task | Observed result |
| --- | --- |
| Init and readiness | 2 public CLI steps; ready and autonomous-vault ready; 0.735643 seconds. |
| Exact Markdown evidence | Caller bytes and Source SHA verified. Living Wiki retained exact Source Revision, content SHA-256, Fragment, Locator, and quote SHA-256 coordinates. Raw Source/Fragment content reads were correctly withheld while the Source remained pending owner review; wrong-state admission was 0. |
| Compilation | 1 object, 1 packet, 7 public CLI steps; validation valid; projection succeeded; 2.898643 seconds. |
| Task Continuity | Enrollment, ordinary resume, fork, compaction, stale checkpoint, wrong task, wrong worktree, and selective forget executed in 14 public CLI steps. Decision preservation was true, Wrong-State Admission was 0, and no transcript was copied. The task Provider Capsule was 1,624 UTF-8 bytes. |
| Current Provider | Advertised only `query`, `context`, and `explain`. Actual stdio MCP query/context content was 1,404/1,404 UTF-8 bytes. Canonical sequence and audit head were unchanged across reads. Native Provider tokens were unavailable because no real Host/model ran. |
| 1k Wiki lane | 1,000 objects in 12 packets and 37 public CLI steps plus one external workspace edit. Total 63.014813 seconds; compile/project 30.292410 seconds; query 10.759126 seconds; browse 10.726518 seconds; peak child RSS 244,695,040 bytes; 6,149 files; 34,982,338 storage bytes; Capsule 1,342 bytes. No-op projection equivalence, rename/edit/reconcile, and unmanaged owner-file exact-byte protection were true. |
| 10k Wiki lane | 10,000 objects in 122 packets and 257 public CLI steps plus one external workspace edit. Total 883.279403 seconds; compile/project 564.316078 seconds; query 104.150140 seconds; browse 115.319551 seconds; peak child RSS 1,333,870,592 bytes; 60,934 files; 328,057,546 storage bytes; Capsule 1,522 bytes. No-op projection equivalence, rename/edit/reconcile, and unmanaged owner-file exact-byte protection were true. |

The full retained run took 967.419489 seconds. Query Gaps remained explicit: the 1k lane reported
`duty_unresolved` and `no_answer`; the 10k lane additionally reported
`compiled_discovery_bound`. These are not rewritten as answers or PASS labels.

## Real P1 findings and minimal corrections

Three public tasks failed before the retained run. Failed and manually interrupted attempts are
excluded from the retained report.

1. A valid 1k Living Wiki manifest exceeded the incremental reader's stale 1 MiB ceiling while
   the canonical manifest contract allows 64 MiB. The reader now uses the existing 64 MiB bound;
   no manifest contract or file limit was weakened.
2. At 10k, one Source page and then `wiki/communities/index.md` exceeded the existing 256 KiB
   derived-file bound because they inlined unbounded links. Source-bound and navigation links now
   reuse the existing 200-item shard bound and emit an explicit omission message; the complete
   objects remain available through existing kind shards, Ledger, query/context, and Source
   coordinates. No page family or database was added.
3. `knowledge reconcile` counted directories and unmanaged files against its 10,000 managed-file
   limit and failed with `workspace reconcile exceeds its entry-count bound` at exactly 10k
   governed objects. Managed Markdown remains capped at 10,000; a separate 11,000-entry scan bound
   now reserves fixed overhead for kind/tier directories and owner files. The 256 MiB total-byte,
   per-file, symlink, unsafe-entry, grant, and CAS checks remain unchanged.

## Four-axis evidence boundary

| Axis | Demonstrated here | Still unverified |
| --- | --- | --- |
| Continuity | Public Task start/locate, task-neutral Host connect, stdin enrollment, ordinary resume, fork, compaction, wrong-state withholding, and selective forget; Decision Preservation true and Wrong-State Admission 0. | Native Codex/OpenCode lifecycle behavior, actual returned model identity, and Host-native token usage. |
| Evidence | Exact Markdown bytes and Source hash; Source Revision/Fragment/Locator/quote-hash coordinates; pending raw content fail-closed. | PDF/DOCX/HTML, owner-reviewed raw Source/Fragment read, effective dates, exceptions/provisos/cross-references, wrong-version/false-Authority fixtures, and signed Legal Pack. |
| Context efficiency | Current Provider surface and actual UTF-8 bytes remained under the 65,536-byte hard limit; read operations did not change the canonical Ledger. | Actual native Provider tokens, duplicate/distractor comparative metrics, and any cross-product efficiency claim. |
| Wiki integrity | Public 1k/10k compile/project, no-op equivalence, browse/query, rename/edit/reconcile, explicit bounded omissions, and unmanaged owner-file exact-byte preservation. | Interrupted changed-input recovery, full-versus-incremental changed-input equivalence, aliases/same-name collision, Source successor, backlink/outlink, wrong merge, Obsidian Desktop UI, and 100k/3 OS behavior. |

## Executed, failed, and not executed

- **Executed:** the retained report's init/doctor, exact Markdown registration/verification,
  source-only Gap, Compilation Run/projection, Task Continuity journey, stdio MCP
  query/context/explain, read no-write audit, and public 1k/10k lanes.
- **Failed before repair:** stale manifest-reader ceiling, unbounded Source/community projection,
  and 10k reconcile entry counting. Each was reproduced at a public seam, minimally corrected, and
  covered by focused regression before the fresh retained run.
- **Not executed:** interruption injection; changed-input full/incremental equivalence;
  PDF/DOCX/HTML; owner-reviewed raw Source/Fragment content; alias/same-name/Source successor;
  backlink/outlink/wrong merge; effective-date and legal-duty cases; real Codex/OpenCode;
  Obsidian Desktop; signed Legal Pack; 3 OS/Python matrix; reproducible wheel/sdist and supply
  chain; Human Gold/legal attestation; isolated external/commercial scoring.

## Separate formal-qualification recovery inputs

These inputs remain required by the release qualification Goal and are all `not_executed` here:

1. owner-controlled external corpus/reference/Host/package inputs with exact SHA-256 identities;
2. exact real Codex and OpenCode binaries, supported no-inspection authentication seams, at least
   three distinct runs per Host, returned model identity, and native Provider usage;
3. isolated reference freezer, candidate Host, scorer A, scorer B, arbiter/attester, credential
   broker, executable/process/mount/network/IPC receipts, and negative canaries;
4. frozen Evidence/Legal/Wiki/Context duties, owner-reviewed Source content, signed Legal Pack,
   hard failures, and source-specific retained outputs;
5. the required 1k/10k/100k release scale profile, 3 OS/required Python matrix, and exact artifact
   public journey;
6. one exact wheel/sdist pair with reproducibility, SBOM, licenses, OpenVEX, provenance, redownload,
   and artifact SHA verification; and
7. fresh Candidate Full, External, and Commercial qualification over the same artifact/input pair,
   including all 14 Core Gates. No result in this document may be reused as those receipts.

## Scope decision

The named research found useful update, recovery, file-protection, ambiguity, and bounded-context
guardrails. Every one fits existing Source Revision, Knowledge Revision, Ledger, Wiki,
reconciliation, Context, receipt, and Task Continuity primitives. No observed task justifies a new
database, Knowledge kind, Relation predicate, page family, Host adapter, Agent runtime, connector,
telemetry path, GUI, second retrieval engine, second Ledger, or second Authority model.

Real Host/model calls remain `not_executed` in this Goal unless the owner already has a supported
authentication seam that needs no credential inspection or copying. Their absence does not block
this development comparison and cannot be rewritten as product evidence.
