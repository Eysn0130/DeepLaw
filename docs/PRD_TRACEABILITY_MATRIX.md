# DeepLaw PRD 1.3 traceability matrix

Status: **current source-candidate mapping**, carried forward from the Pass 5 full mapping and
updated 2026-08-11 with the Pass 8 disposition and PRD 1.3 evidence/Wiki boundary clarification.
This is not a fresh qualification, release note, or permission to implement every Target.

Pass 11 current pointers: Pass 10 evidence is invalidated without rewriting its historical files
by [`V0_13_PASS10_CURRENT_DISPOSITION.md`](V0_13_PASS10_CURRENT_DISPOSITION.md). Candidate task
inputs and evaluator-only Gold/scoring are physically separated. Three Codex App Server
token-attribution workflows ran, but all were partial/failed candidate evidence; no profile was
admitted and the authorized Codex workflow budget is exhausted. See
[`V0_13_PASS11_TOKEN_ATTRIBUTION_DISPOSITION.md`](V0_13_PASS11_TOKEN_ATTRIBUTION_DISPOSITION.md).
No real-Host qualification passed. The default CLI product journey, layered Help, direct reconcile alias,
read-only Host connection plan, and frozen caller/contract preservation boundary are recorded in
[`../governance/product-surface-manifest.v1.json`](../governance/product-surface-manifest.v1.json)
and [`V0_13_PASS11_CALLER_CONTRACT_INVENTORY.md`](V0_13_PASS11_CALLER_CONTRACT_INVENTORY.md).
Run Timeline and semantic restore remain `not_claimed` as recorded in
[`V0_13_PASS11_TIMELINE_RESTORE_DISPOSITION.md`](V0_13_PASS11_TIMELINE_RESTORE_DISPOSITION.md).

## Frozen audit boundary

- PRD revision: `1.3`; SHA-256:
  `f83bf5a9b29eef9b0af80034b2190266f1ba74edc5c56f422289ab3a49aed9b5`.
- Upstream research SHA-256:
  `00dfab0dfed139f5d81982061a75896f29552f56a125aa83bec57f0c6a860967`.
- Pass 8 reviewed upstream coordinates are OpenWiki
  `7531d615216e8cbccf464f66cfbbae3668871c84` (`v0.3.1`) and Tolaria
  `ab01faa6773136a58285d04cb81e2587c11bac85`. These are frozen review/external-probe
  coordinates, not dependency pins or assertions about either upstream's current HEAD.
- Branch: `codex/v013-pass8-lean-qualification`; Pass 8 final code candidate commit/tree:
  `2a635d228e99537304282223ae08ef066a4961e2` /
  `566b2f546816264d997f1418c61be6c25cdb2494`. The pre-PRD-1.3 documentation HEAD/tree is
  `2dd0e8a0b97239eb28cd0e8ca9a2c939363a2f59` /
  `a3340c2364407269868ca99be61c4bd63b98e7f8`.
- The Owner's prior work was preserved. The remediation is split into failure reproductions,
  routing/snapshot correction, sink compatibility, route-first retrieval, bounded diagnostics,
  repository-development protocol rotation, and the v0.13 release gate. No reset, force push,
  version bump, tag, or publication occurred.
- Package version: `0.12.0`. Legal release/storage schemas: `deeplaw.release/v3` and
  `deeplaw.sqlite/v6`. Knowledge Vault/storage schemas: `deeplaw.knowledge-vault/v1` and
  `deeplaw.knowledge-sqlite/v1`. Autonomous migration head:
  `deeplaw.autonomous-knowledge-core/v2` in the `autonomous_core_v3` table family; Source,
  Semantic, and Statement Evidence compilation cores remain additive v1 migrations. There is no
  standalone `migrations/` directory; migration, verified backup, recovery, and rollback are
  implemented in the domain stores.
- Current source-candidate read surfaces are Query Plan v6, local Capsule v3, Provider Capsule v2,
  and MCP `knowledge-support-output/v6`; the mutation surface is
  `knowledge-sink.input/v5` with frozen v2 compatibility. The published package remains v0.12.0;
  these candidate surfaces are not thereby released as v0.13.
- Pass 8 final code-candidate baseline before this documentation rotation:
  `uv run --frozen pytest --strict-markers` → `1427 passed, 6 skipped`.
- Historical repository Gold freeze inputs are inventoried by
  `benchmarks/quality/repository-gold-v1.json`: `README.md`, `AGENTS.md`, `README_EN.md`,
  `docs/AGENT_ADAPTERS.md`, `src/deeplaw/knowledge_autonomy.py`,
  `src/deeplaw/knowledge_intelligence.py`, `docs/DEEPLAW_2.md`,
  `contracts/law-federated-context.v1.schema.json`, `docs/ARCHITECTURE.md`, and
  `docs/AUTONOMOUS_KNOWLEDGE_OS.md`. v1 remains byte-for-byte historical and correctly rejects the
  current changed runtime/docs boundary. The current default uses explicit repository-visible
  development Gold v3 and evaluation protocol v2; neither is external, independent, blind, or
  claim eligible.
- The v0.12 repository temporal holdout is public and label-visible. The v0.13 qualification and
  final-blind holdout hashes, candidate wheel hash, and source-candidate binding remain unset.
  Repository-external Gold content was not read in this audit.

## Continuity Pass 2 development boundary

Pass 2 is a follow-up to the retained **Pass 1** implementation boundary above. Pass 1 hashes,
historical Gold/protocol inputs, and local evidence remain historical and are not rewritten. The
continuity correction is commit `2f31bff4069e6cf01edf017134e5a760becb5360`; the semantic
release-evidence correction is commit `d7da1869287fd590d820f7dd60506abdcb826ad4`.
This tracked matrix cannot bind its own final tree, and no qualification wheel or external report
hash exists. The commits are development evidence only and are not a release or qualification
result.

Three reproduced defects and minimum repairs are mapped here before the row-level status matrix:

| Root cause | Minimum repair and invariant | Evidence/status boundary |
|---|---|---|
| Exact route candidate could be displaced by ordinary selection | Reserve one exact route candidate as a separate bounded admission partition; the no-route ceiling remains `512`, one reservation leaves at most `511` ordinary candidates, and the combined/global budget is unchanged | Development kernel only; E2E `Target`; external qualification `not_executed` |
| Retrieval goal changed route identity | Retrieval query is `task + goal`; route digest is generated only from canonical task text inside the domain | Development kernel only; adapters do not derive route identity; external qualification `not_executed` |
| One route could have multiple current heads | First route write creates one Knowledge Object; later writes create a new revision with `expected_revision` CAS; stale/concurrent writes are `checkpoint_head_conflict`; pre-fix multi-head reads are a sanitized Gap and Owner `forget`/withdraw + projection rebuild reconciles them, never LWW | Development kernel only; E2E `Target`; external qualification `not_executed` |

The route projection is derived/rebuildable. The continuity correction introduces no new
canonical Knowledge table, migration, or sink schema, and `knowledge-sink.input/v2` bytes remain
unchanged. This is a semantic compatibility boundary only. Core gates are not lowered; deferred
Capability gates may remain `not_claimed` when
not declared (Timeline, semantic restore, and Claude/OpenCode), while the Competitive Claim gate is
independent of kernel evidence.

## Status and evidence rules

Only the PRD states `Target`, `Implemented`, `Qualified`, `Released`, `Deferred`, and
`Not Implemented` are used below. `Implemented` means the current runtime and public seam contain
the mapped behavior with repository regression evidence; it does not mean the behavior passed an
independent Human Gold, real-Host, exact-Pack, scale, portability, or release-artifact gate.
`Qualified` requires capability-specific external evidence, and `Released` requires a published
artifact bound to that evidence. No PRD 1.3 row is marked `Qualified` or `Released` in this audit.

Evidence abbreviations in the table are repository-relative paths:

- `KA`: `src/deeplaw/knowledge_autonomy.py`; `KS`: `src/deeplaw/knowledge_store.py`;
  `KC`: `src/deeplaw/compilation/coordinator.py`; `CS`: `src/deeplaw/compilation/store.py`.
- `Q6`: `src/deeplaw/retrieval/query_v6.py`; `CAP`:
  `src/deeplaw/retrieval/capsule.py`; `KOS`: `src/deeplaw/api/knowledge_os.py`;
  `KMCP`: `src/deeplaw/knowledge_mcp_server.py`.
- `TC`: `src/deeplaw/task_context.py` and
  `contracts/task-context-binding.v1.schema.json`.
- `TR`: the rebuildable `knowledge_checkpoint_routes_v1` projection in `KA`; `SINK5`:
  `contracts/knowledge-sink.input.v5.schema.json` with frozen v2 compatibility.
- `WIKI`: `src/deeplaw/projection/`, `src/deeplaw/wiki/`, and
  `src/deeplaw/wiki_coverage.py`; `LEGAL`: `src/deeplaw/store.py`,
  `src/deeplaw/search.py`, and `src/deeplaw/mcp_server.py`.
- `ARCH`: `docs/ARCHITECTURE.md`; `AKO`: `docs/AUTONOMOUS_KNOWLEDGE_OS.md`;
  `DISP`: `docs/V0_13_PASS8_RELEASE_DISPOSITION.md`; `UP`:
  `docs/V0_13_UPSTREAM_RESEARCH.md`.

## Problem and principle mapping

| PRD ID | Capability | Current status | Runtime code | Contract/Schema | Tests | External evidence | Limitation | Required next action |
|---|---|---|---|---|---|---|---|---|
| PRD-PROBLEM-001 | Correct cross-thread handoff without task-line contamination | Target | KA, KOS, Q6, TC | task binding v1; Capsule v3; provider v2 | core continuity + task binding/lineage/worktree regressions | Wrong task-line admission reproduced with real linked worktrees and an installed wheel; minimum remediation passes development regression | Independent Gold, native-memory comparison, fork lifecycle, and real Hosts absent | Run fresh external continuity qualification; do not broaden the remediation |
| PRD-PROBLEM-002 | Minimum useful context instead of long noisy context | Target | Q6, CAP | Query Plan v6; Capsule v3 | `test_v013_query_v6.py`, `test_v013_quality_metrics.py` | DISP ratio `0.760628 < 0.8` | Frozen efficiency threshold failed | Use fresh Human Gold; repair only a reproduced noise cause |
| PRD-PROBLEM-003 | Compile reusable knowledge instead of repeated raw-fragment RAG | Target | KC, CS, Q6 | compilation/Statement v1 contracts | `test_source_compilation.py` | None independent | Candidate implementation is not external task proof | Run source-update and reuse holdout tasks |
| PRD-PROBLEM-004 | Human and Agent share an evidence-linked Wiki | Target | WIKI, KMCP | registry/link/manifest contracts | `test_v013_evidence_wiki_benchmark.py` | DISP Wiki development pass | No independent human usability run | Execute frozen human Wiki task without new UI |
| PRD-PROBLEM-005 | Keep evidence, interpretation, provenance, and Authority distinct | Target | KA, LEGAL | Knowledge v3; legal evidence v2 | autonomy and legal tests | Legal development qualification failed | Exact signed Pack and independent legal Gold absent | Obtain verified Pack and legal Gold |
| PRD-PROBLEM-006 | Reject poisoning, false state, scope escape, and secret persistence | Target | KA, KMCP, host harnesses | SINK5 with frozen v2; provider gates | security and host-isolation tests | Fake-host canary only | Real isolated Hosts not executed | Run preflight and real Hosts only after Owner prerequisites |
| PRD-PROBLEM-007 | Preserve temporal change, supersession, and stale-state gaps | Target | KA, KC, Q6 | revision/relation/query-plan contracts | temporal/freshness tests | None independent | Semantic restore and stale-head user tasks unqualified | Freeze temporal and restore tasks |
| PRD-PROBLEM-008 | Fail closed across Vault, project, worktree, and task lines | Target | KA, KOS, Q6, TC | task binding v1 plus existing Vault/scope contracts | PRD 1.3 binding/lineage/worktree/Vault regressions | Same-Vault wrong-line reproduced then remediated in development; default physical cross-Vault leak not reproduced | Explicit cross-Vault references and full fork/conflict lifecycle remain unqualified | Freeze external lineage and explicit cross-Vault tasks |
| PRD-PRINCIPLE-001 | Minimum sufficient context | Target | Q6, CAP | Capsule v3/provider v2 | quality/context tests | Continuity development score | Context-density threshold failed | Fresh holdout and task-success comparison |
| PRD-PRINCIPLE-002 | Evidence before interpretation | Implemented | KA, KC, LEGAL | evidence/Statement/legal contracts | source, Statement, legal tests | Wiki development chain | Legal qualification failed | Exact-Pack and legal Human Gold qualification |
| PRD-PRINCIPLE-003 | One governed system with isolated policy planes | Implemented | KS, KA, KC, LEGAL | shared identity/revision contracts | autonomy/source/legal tests | None independent | Physical-plane parity not externally qualified | Keep shared semantics in every future repair |
| PRD-PRINCIPLE-004 | Knowledge never grants control authority | Implemented | KA, sink/read MCP split | SINK5/frozen v2 and read schemas | sink/security tests | None independent | Same-owner shell remains outside MCP boundary | Preserve Host/OS isolation in real runs |
| PRD-PRINCIPLE-005 | Progressive disclosure and drill-down | Implemented | Q6, CAP, KMCP | provider v2, source read schemas | context/source tests | Wiki development chain | Real provider task evidence absent | Measure task correctness and provider bytes |
| PRD-PRINCIPLE-006 | Open work surface over a trusted kernel | Implemented | KA, WIKI | Knowledge v3, manifest/registry | projection/ownership tests | Local-only adapter evidence | Obsidian/Tolaria product E2E pending | Run human/editor tasks before widening surface |
| PRD-PRINCIPLE-007 | Derived state is replaceable | Implemented | CS, WIKI, knowledge intelligence | derived manifest v2 | rebuild/recovery tests | Local synthetic only | Current-candidate scale and 3-OS evidence absent | Qualify rebuild equivalence and resource bounds later |
| PRD-PRINCIPLE-008 | Contradiction, exception, uncertainty, and gaps are first-class | Implemented | Q6, CAP, KA | plan/capsule/relation schemas | graph/context/legal tests | Development legal Gap only | Human/legal coverage not qualified | Freeze contradiction/exception/Gap Gold |
| PRD-PRINCIPLE-009 | Complexity requires external failure and existing-primitive analysis | Implemented | Development process, not runtime | PRD feature-admission rule | This matrix and Task Cards | No qualifying external failure yet | No runtime expansion is admitted | Keep new runtime work frozen |
| PRD-PRINCIPLE-010 | Stable invariants, replaceable retrieval algorithms | Implemented | KA, Q6, WIKI | identity/provenance/plan manifests | identity/retrieval/rebuild tests | None comparative | Named comparators not executed | Retain invariant checks in any retrieval experiment |
| PRD-PRINCIPLE-011 | Correctness belongs to the full state trajectory | Target | KA, KS | lifecycle/event/snapshot contracts | lifecycle/recovery tests | Local development only | Concurrent lineage and semantic restore incomplete | Freeze write-update-forget-restore trajectory tasks |
| PRD-PRINCIPLE-012 | Newest is not necessarily relevant | Target | Q6, KA | plan v6, temporal revisions | tail/temporal tests | None independent | Cross-task-line and disambiguation mapping incomplete | Reproduce wrong-newest admission across public seams |

## Continuity mapping

| PRD ID | Capability | Current status | Runtime code | Contract/Schema | Tests | External evidence | Limitation | Required next action |
|---|---|---|---|---|---|---|---|---|
| PRD-CONT-001 | Bounded current Task Checkpoint with TTL and exact Run/task binding | Implemented | KA, Q6, CAP, TC, TR | Knowledge v3; task binding v1; SINK5; Capsule v3 | core continuity + route-first/task-binding regressions | Development kernel only | No independent Gold/Host | Freeze repository-external continuity Gold |
| PRD-CONT-002 | Exclude transcript, hidden reasoning, raw logs, credentials, tokens, paths | Implemented | KA, CAP, projection gate | sink/provider schemas | continuity/security tests | Canary on fake hosts | Real-host proof absent | Repeat canary on isolated real Hosts |
| PRD-CONT-003 | Exclude inactive, mismatched, or unbound checkpoints | Implemented | KA admission, Q6, TC | lifecycle/task-binding/query-plan schemas | autonomy/core-continuity/task-line tests | Exact mismatch and absent binding pass development regressions | Full state-trajectory holdout absent | Add expired/failed/aborted external task cases |
| PRD-CONT-004 | Checkpoints remain Agent-derived and non-authoritative | Implemented | KA | Knowledge Object v3 | authority tests | None independent | No real Host measurement | Retain zero Authority elevation gate |
| PRD-CONT-005 | Hooks cannot mint grants or hide writes | Implemented | adapter envelopes; sink split | Agent Context v1; SINK5 with frozen v2 | agent-context/sink tests | Static/local-only adapters | Real Host lifecycle not executed | Verify resolved Host configuration |
| PRD-CONT-006 | Cold thread restores through one Context seam | Target | KOS, KMCP, CAP, TR exact-task kernel | Capsule v3/provider v2 | context parity, exact unique/ambiguous cold-start regressions, Pass 2 route/goal regressions | Kernel `Implemented` in development; E2E `Target`; external qualification `not_executed` | No semantic resolver, stable real-Host derivation, Human Gold, or real Host | Compare Host-only, Host Memory, Host+DeepLaw on a frozen external task set |
| PRD-CONT-007 | Complement Host memory without scraping or copying it | Implemented | adapter and Context boundaries | Agent Context v1 | cross-host context tests | Static/local-only | Host memory comparison not executed | Freeze equal-budget native-memory experiment |
| PRD-CONT-008 | Ground continuity in durable project state and artifact references | Target | KA, TC, TR kernel | Run Record/event plus task binding v1 and SINK5 | core continuity + real-worktree/reconciliation regressions, Pass 2 route/CAS recovery regressions | Kernel `Implemented` in development; E2E `Target`; external qualification `not_executed` | Stable Host-neutral ID enrollment, real Artifact lifecycle, and independent task evidence absent | Qualify with real concurrent Hosts/worktrees |
| PRD-CONT-009 | Optional future intention, never a scheduler | Deferred | None | None | None | None | No admitted user failure; not core v0.13 | Revisit only through feature admission |
| PRD-CONT-010 | Bind Vault/project/task lineage/repo/worktree/base/dirty state | Target | KA, Q6, CAP, TC, TR kernel | route/snapshot binding v1; Query Plan v6; Capsule v3 | task routing, divergence, lineage, real-worktree regressions, Pass 2 route reservation/goal identity/head-conflict regressions | Kernel `Implemented` in development; E2E `Target`; external qualification `not_executed` | Top-20 loss and silent divergence reproduced; exact route reservation keeps a combined ceiling of `512` (`511` ordinary plus one exact route); real Host-neutral identity derivation, fork reconciliation, independent Gold, and full external scale remain absent | Run a fresh unseen concurrent-worktree holdout |
| PRD-CONT-011 | Independent concurrent/fork task-line current state and explicit conflicts | Target | Exact-line read isolation only; no merge coordinator | task binding v1 preserves optional opaque parent only | two-line regressions | Concurrent current lines no longer cross-admit in development | Fork/merge/conflict reconciliation lifecycle remains not_executed | Freeze external fork/conflict Gold before any coordinator |
| PRD-CONT-012 | Content-minimized searchable Run Timeline | Not Implemented | Run records/events are primitives only | No Timeline schema/API | `test_prd12_run_timeline_reproduction.py` | `reproduced_missing_public_seam` | No owner filtering/search/deletion surface | Freeze external time-to-locate and forget Gold |
| PRD-CONT-013 | Treat Host/session/memory references as untrusted hints | Implemented | adapter envelope/admission | Agent Context v1 | cross-host/context tests | Static adapter evidence | No real Host malicious-hint run | Include wrong Host reference in holdout |
| PRD-CONT-014 | Bounded bootstrap → drill-down → explicit Checkpoint lifecycle | Target | KMCP, KOS, TR, sink split kernel | Context/Capsule/SINK5 contracts | context, route-first, legacy reconciliation, Pass 2 single-head/CAS/recovery tests | Kernel `Implemented` in development; E2E `Target`; external qualification `not_executed` | No owner UI/real Host lifecycle/Human Gold; Timeline absent; pre-fix multi-head reads are sanitized Gaps and Owner reconciliation is not LWW | Measure provider bytes and First Correct Action end to end |

## Source and governed knowledge mapping

| PRD ID | Capability | Current status | Runtime code | Contract/Schema | Tests | External evidence | Limitation | Required next action |
|---|---|---|---|---|---|---|---|---|
| PRD-SRC-001 | Immutable Source Revision; changed bytes create successor | Implemented | KS, KC | source revision/compilation contracts | source-compilation tests | Wiki development chain | No current-candidate external source corpus | Run successor task on fresh authorized sources |
| PRD-SRC-002 | Preserve order, structure, fragments, locators, hashes, parser provenance | Implemented | KS, KC | Source IR/fragment/locator contracts | source/parser tests | Wiki development chain | Legal exact Pack pending | Qualify exact bytes and locators |
| PRD-SRC-003 | Model proposes; deterministic code governs commit | Implemented | KC, KA | closed Plan and sink schemas | semantic/coordinator tests | Fake-Agent only | Real model proposal not executed | Run isolated real Host after prerequisites |
| PRD-SRC-004 | Selectively invalidate true dependents | Implemented | KC freshness, WIKI incremental | dependency/freshness contracts | semantic/incremental tests | Local synthetic | Current 10k/100k qualification deferred | Run product Gold first, then scale if justified |
| PRD-SRC-005 | Idempotent unchanged ingest and semantic identity reuse | Implemented | KC, KA reconciliation | identity/idempotency contracts | source/identity tests | None independent | Real duplicate corpus unqualified | Add authorized repeated-ingest task |
| PRD-SRC-006 | Explicit withdrawal, supersession, forgetting, private erasure | Implemented | KS, KA | lifecycle/tombstone contracts | lifecycle/GC tests | Local only | Selective forgetting Human Gold absent | Freeze write-execute-forget task |
| PRD-SRC-007 | Treat compilation as lossy and expose coverage gaps | Implemented | KC, Q6, WIKI coverage | duty/gap contracts | coverage/semantic tests | Wiki development pass | Coverage Spec is a validation kernel, not full Guides | Run human missing-coverage task |
| PRD-SRC-008 | Targeted refinement only; no unbounded regeneration/injection | Implemented | KC, Q6 | bounded compilation/query schemas | semantic/query tests | None independent | No external refinement task | Preserve bounds in future failure reproduction |
| PRD-SRC-009 | Bounded owner-visible source acquisition manifest | Implemented | `source_connectors.py`, `source_adapters.py` | source snapshot contracts | source connector/security tests | Local-only | No external connector qualification | Run allowlist/exclusion/provenance task |
| PRD-SRC-010 | Connector cannot create identity or Authority | Implemented | source connectors, KS admission | snapshot/source identity schemas | connector/authority tests | Local-only | No adversarial external acquisition | Test alias/path/URL collisions |
| PRD-SRC-011 | Keep professional/authoritative documents source-native with exact Document/Version/Locator identity | Target | Foundation implemented in Evidence Core, KS, LEGAL, and Document IR | source/document/legal schemas | source, parser, document and legal tests | Journey unqualified; local foundation evidence only | Broad professional-format, Wiki-to-Source drill-down, and exact Legal Pack qualification remain absent | Run source-native PDF/DOCX/HTML and exact legal tasks without full-Wiki transcription |
| PRD-SRC-012 | Treat OCR/layout/search accelerators as revision-bound replaceable state | Implemented | document pipeline, derived indexes | parser/derived manifests | parser, OCR-risk, rebuild tests | Local development only | Critical-token and multi-parser external corpus unqualified | Freeze critical-token mutation and rebuild-equivalence Gold |
| PRD-KNOW-001 | Every mutation creates revision and audit event | Implemented | KA coordinator | Knowledge revision/event schemas | autonomous/audit tests | None independent | Same-owner OS compromise out of scope | Retain replay/integrity hard gate |
| PRD-KNOW-002 | Separate owner-granted knowledge_sink | Implemented | sink server, KA | SINK5 with frozen v2 compatibility | sink MCP/contract tests | Local stdio only | Real Host grant isolation pending | Verify resolved tool set and OS isolation |
| PRD-KNOW-003 | Read/query/context/Wiki/law surfaces contain no hidden writes | Implemented | KMCP, KOS, LEGAL | read MCP schemas | MCP/no-write tests | Local stdio only | Real host not executed | Repeat Ledger-head no-write checks in Hosts |
| PRD-KNOW-004 | Reuse semantic identity and preserve merge/split/contested lineage | Implemented | KA identity resolution | identity/relation contracts | identity/alias tests | Local only | Cross-Vault disambiguation incomplete | Freeze wrong-merge and alias-collision Gold |
| PRD-KNOW-005 | Keep origin, verification, Authority, lifecycle, scope, and time separate | Implemented | KA | Knowledge Object/Revision/Relation v3/v2 | authority/temporal tests | None independent | No external governance audit | Retain dimension parity tests |
| PRD-KNOW-006 | Ranking and usage cannot create Authority or permission | Implemented | admission/rerank paths | plan/knowledge contracts | authority/reranker tests | None independent | Real-model poisoning not executed | Add adversarial holdout |
| PRD-KNOW-007 | Consolidation preserves lineage, contradiction, and deletion semantics | Implemented | KA consolidation saga | consolidation/relation contracts | consolidation/forget tests | Local only | Full lifecycle Gold absent | Run selective-forgetting trajectory |
| PRD-KNOW-008 | Feedback evaluates outcome but cannot promote policy dimensions | Implemented | KA feedback | feedback ledger v1 | feedback/skill tests | Local only | Independent evaluator path unqualified | Freeze external evaluator task |
| PRD-KNOW-009 | Measure duplicate/stale/orphan/broken/invalidation maintenance debt | Target | partial lint/gap/rebuild queues | gap/lint/manifest contracts | lint/rebuild tests | None | No frozen debt metric suite | Define metrics only after user-task failure |
| PRD-KNOW-010 | Explicit Vault/project isolation and independent lifecycle operations | Target | physical Vault isolation; partial scope admission | current schemas lack cross-Vault contract | `test_prd12_cross_vault_isolation_reproduction.py` | `not_reproduced_default_physical_isolation` | Explicit reference/import/export and lifecycle independence remain unqualified | Freeze explicit cross-Vault task before any feature |

## Living Wiki mapping

| PRD ID | Capability | Current status | Runtime code | Contract/Schema | Tests | External evidence | Limitation | Required next action |
|---|---|---|---|---|---|---|---|---|
| PRD-WIKI-001 | Evidence-labelled readable Markdown projections | Implemented | WIKI | Wiki manifest/registry contracts | projection/page tests | Wiki development pass | Statement resolver deferred | Execute human drill-down task |
| PRD-WIKI-002 | Human and Agent share the same semantic link network | Implemented | WIKI, KMCP | Link Index/Resolver schemas | link/resolver/MCP tests | Development chain only | No independent human task | Freeze human/Agent paired navigation Gold |
| PRD-WIKI-003 | Wiki surface metadata cannot create identity or Authority | Implemented | WIKI, KA reconciliation | registry/Knowledge contracts | ownership/authority tests | Local only | Editor product E2E absent | Test frontmatter/Wikilink attacks in editor task |
| PRD-WIKI-004 | Rename/move preserves ID; content edit creates revision | Implemented | KA reconcile, WIKI registry | Knowledge Revision v2 | reconcile/registry tests | Local only | Real editor concurrent edit pending | Run authorized editor task |
| PRD-WIKI-005 | Registry/Link Index/Resolver avoid warm filesystem scans | Implemented | `wiki/registry.py`, `link_index.py`, `resolver.py` | v3 registry/index contracts | registry/link/resolver tests | Local only | Human and current-candidate scale unqualified | Measure bounded lookup after product Gold |
| PRD-WIKI-006 | Full/incremental equivalence and user-file preservation | Implemented | projection builder/incremental | manifest/journal schemas | incremental/ownership/recovery tests | Local synthetic | 3-OS/current scale not executed | Run equivalence and owner-file task |
| PRD-WIKI-007 | Optional Guides/Codemaps/Canvas/community/materialized paths | Deferred | Existing derived views only | Existing manifests | page-family tests | None | Core Scope forbids expansion | Revisit in v0.14+ after external failure |
| PRD-WIKI-008 | Obsidian/Tolaria/future GUI remain clients of shared services | Implemented | shared-service editor adapters only | adapter manifests | editor bridge/living-wiki delivery tests | Obsidian local-only; Tolaria integration-limited | Client boundary exists, but real desktop/product E2E and any future GUI are deferred | Do not claim product integration; run explicit E2E later |
| PRD-WIKI-009 | Shared projection may expose unequal Authority and reconcile edits | Implemented | WIKI, KA | registry/Knowledge contracts | projection/reconcile tests | Local only | No independent editor audit | Pair human edit with exact revision verification |
| PRD-WIKI-010 | Page exposes machine identity/current revision and human status dimensions | Target | partial page frontmatter/registry | current Wiki schemas | page/registry tests | Development Wiki pass | Ownership classes and full E2E mapping incomplete | Freeze page-comprehension task |
| PRD-WIKI-011 | Wikilinks/backlinks are navigation; typed Relation Revisions assert semantics | Implemented | WIKI link index, KA relations | Knowledge Relation v3 | link/graph/relation tests | Local only | No external wrong-relation task | Add co-occurrence negative Gold |
| PRD-WIKI-012 | Distinguish protected, derived, governed-editable, and user-owned pages | Target | partial ownership manifest/reconcile | manifest/Knowledge contracts | ownership tests | Local owner-file evidence | Complete ownership class semantics not proven | Freeze protected/editable/user-file human task |
| PRD-WIKI-013 | Bounded typed neighborhood/path with provenance, time, hops, truncation, Gap | Target | bounded graph read only | relation/query-plan schemas | graph tests | None | No true explanatory Relation Path API | Reproduce a core path task before implementation |
| PRD-WIKI-014 | Project protected sources as bounded cards/links, not editable canonical transcriptions | Target | partial source/evidence projections | Wiki/source manifests | projection/ownership tests | Local only | General professional Evidence Library journey and human drill-down are unqualified | Freeze paired Wiki-to-exact-Source task before expanding page families |
| PRD-WIKI-015 | Bound fine-grained physical persistence across compilation, evidence, and projection | Target | Statement evidence CAS plus current sharded Wiki projection | Statement/receipt and page/registry/manifest contracts | Statement evidence, 1k Wiki, and focused shard tests | Pass 8 100k Statement construction diagnostic is claim-ineligible | The diagnostic used 310,116 files and 1,188,124,770 bytes; the known implementation writes `statement`, `statement_map`, and `statement_evidence_receipt` CAS objects per Statement, but the artifact-family share still requires a fresh public-path inventory. Wiki 10k/100k and Relation physical profiles were not executed, so neither is assigned the same root cause. | Reproduce 1k/10k artifact-family counts through public paths, then qualify a bounded physical profile without changing identity, receipt, replay, recovery, or visible semantics |

## Context and retrieval mapping

| PRD ID | Capability | Current status | Runtime code | Contract/Schema | Tests | External evidence | Limitation | Required next action |
|---|---|---|---|---|---|---|---|---|
| PRD-CTX-001 | `knowledge context` is recommended Agent seam; query is diagnostic | Implemented | KOS, KMCP, knowledge CLI | plan v6; Capsule v3/provider v2 | v6 context parity tests | Static/local-only | Real Host not executed; legacy consumers retained | Complete consumer inventory before deprecation |
| PRD-CTX-002 | Discovery, admission, selection, Authority, adjudication remain separate | Implemented | Q6, CAP, KA | Query Plan v6 | query/admission tests | None independent | Human task quality absent | Retain stage-specific receipts |
| PRD-CTX-003 | Independent item/source/char/token/hop/payload budgets | Implemented | Q6, CAP | plan v6/provider v2 | budget/context tests | Local only | Resource claims not externally qualified | Freeze budgets before holdout |
| PRD-CTX-004 | Provider receives only admitted minimal context and opaque receipt | Implemented | CAP | provider capsule v2 | provider projection tests | Fake host only | Real provider canary blocked | Run isolated provider only after Owner gate |
| PRD-CTX-005 | Exclude rejected text/scores/debug/graph/session/secrets/paths | Implemented | CAP, projection gate | provider v2 | security/query-trace tests | Fake-host canary | No real Host evidence | Repeat recursive canary in every Host |
| PRD-CTX-006 | Compiled-to-raw fallback is bounded and visible | Implemented | Q6 | plan v6/receipt | query/evidence fallback tests | Local only | No human citation duty task | Freeze fallback-specific Gold |
| PRD-CTX-007 | Missing, contested, temporal, or out-of-scope evidence becomes Gap | Implemented | Q6, CAP | plan/capsule schemas | temporal/legal/context tests | Legal development Gap | Exact Pack qualification failed | Run verified temporal legal Gold |
| PRD-CTX-008 | Query Trace is bounded, redacted, verified, deletable, non-canonical | Implemented | persistent read runtime/KMCP | process-local trace contract | query-trace tests | Local only | No durable trace by design | Keep process-local unless external need is proven |
| PRD-CTX-009 | Receipt enables local re-resolution without provider expansion | Implemented | Q6, CAP, KMCP | statement evidence/provider receipt | receipt/verify tests | Local only | Real Host join flow absent | Run receipt drill-down task |
| PRD-CTX-010 | Judge context by downstream success, evidence duties, and efficiency | Target | benchmark/scorer tools only | repository development protocol v2; external protocol absent | quality metric tests | Visible development fixture only; prior continuity density failed | Independent Gold and model tasks absent | Freeze equal-budget external outcome protocol |
| PRD-CTX-011 | Stateless retry binds explicit version/scope/task/budgets/truncation | Implemented | Q6, TC | plan v6 + task binding v1 | plan parity + task binding regressions | Local deterministic development only | Real Host retry/expiry evidence absent | Run equal-input retry and changed-head Host task |
| PRD-CTX-012 | Capability discovery distinguishes read, diagnostics, and granted mutation | Implemented | KMCP/sink MCP/LEGAL | closed MCP schemas | stdio schema tests | Local no-model lifecycle | Resolved real-host config pending | Verify exact tool list in isolated Hosts |
| PRD-CTX-013 | Eligibility is independent of row/file/import/ID order | Target | Q6 revision discovery then bounded statements | plan v6 | tail/P0/scale tests | 5,001 regression executed; current 10k/100k lanes are `not_executed` | Order invariance is not mapped at every public seam | Run 10k/100k and complete public-seam mapping before implementation status |
| PRD-CTX-014 | Capsule binds Vault/project/task-line/head/revisions/policy and detects stale head | Target | CAP, Q6, TC, TR kernel | Capsule v3/plan v6/task binding v1 | stale-runtime, route-first, snapshot-divergence, Pass 2 reservation/goal/head-conflict tests | Kernel `Implemented` in development; E2E `Target`; external qualification `not_executed` | Local exact-route/snapshot kernel passes; stale/head-conflict Gaps are bounded and redacted; full changed-head re-resolution, real Host derivation, and fresh external Gold absent | Run changed-head re-resolution holdout |
| PRD-CTX-015 | Ambiguity fails closed and exposes only admitted disambiguation | Target | identity admission, partial target checks | identity/plan schemas | identity/query tests | Local only | Cross-Vault/project/task ambiguity not complete | Freeze ambiguity matrix |

## Protected/legal evidence and security mapping

| PRD ID | Capability | Current status | Runtime code | Contract/Schema | Tests | External evidence | Limitation | Required next action |
|---|---|---|---|---|---|---|---|---|
| PRD-EVID-001 | Protected Source bytes are immutable; lifecycle is explicit | Implemented | LEGAL, KS | release/source lifecycle schemas | legal/source tests | Historical v0.12 release only | v0.13 exact Pack not executed | Run exact verified Pack gate |
| PRD-EVID-002 | law_support is separate and read-only | Implemented | legal MCP | law MCP schemas | MCP/legal tests | Local stdio | Real Hosts not executed | Verify one-leaf tool list in each Host |
| PRD-EVID-003 | Official and private legal stores remain physically/governably isolated | Implemented | LEGAL | federated context v1 | federation/isolation tests | Local only | No current Pack/real Host | Run cross-store adversarial Gold |
| PRD-EVID-004 | Preserve exact document/version/segment/locator/quote/hash/time/receipt | Implemented | LEGAL | evidence card v2/federated v1 | citation/version tests | Development legal candidate | Unsigned, temporally unverified Pack | Obtain signed or equivalent verified material |
| PRD-EVID-005 | Agent interpretation always has `legal_authority=false` | Implemented | KA and legal federation | Knowledge/Legal schemas | authority tests | Local only | Human review does not promote Source Authority | Retain hard-zero gate |
| PRD-EVID-006 | False Authority, wrong version, invalid locator/quote, mutation are zero-tolerance | Implemented | LEGAL verification/admission | legal evidence schemas | legal hard-failure tests | Development hard zeros only | Current/exception primary evidence absent | Run independent legal Gold on exact Pack |
| PRD-EVID-007 | Unverifiable version/temporal chain returns Gap | Implemented | LEGAL, Q6 | federated/context schemas | temporal/no-answer tests | Development Gap result | No verified temporal chain | Qualify Gap precision/recall |
| PRD-EVID-008 | DeepLaw supplies evidence/context, not legal judgment | Implemented | legal read surface | law MCP schemas | contract/negative-operation tests | Local only | Model task not executed | Keep adjudication outside Host prompts/tools |
| PRD-EVID-009 | Use source-first retrieval for quote/version/time/exception/proviso/cross-reference/completeness duties | Target | Partial Q6 duty/fallback and LEGAL foundation | query-plan/evidence-card schemas | duty, citation, temporal and fallback tests | Development foundation only | Public-path tasks have not yet shown that every duty materializes the exact admitted Source Revision and Locator; exact Legal Pack, Human Gold, OCR-critical-token, and real Host runs are absent | Reproduce all named duties, require exact passage materialization or an acceptable Gap, and retain zero wrong-version/locator/Authority failures |
| PRD-SEC-001 | Local canonical state; no implicit upload/telemetry/background cloud | Implemented | stores, explicit connectors | snapshot/manifest contracts | no-network/security tests | Local only | Same-owner external tools out of boundary | Verify clean install and network policy later |
| PRD-SEC-002 | Treat all imported and retrieved content as untrusted data | Implemented | KA risk gate, projection gates | sink/Knowledge schemas | injection/quarantine tests | Local only | Real-model poisoning absent | Add adversarial Human Gold task |
| PRD-SEC-003 | Closed Host environment and complete Provider-secret isolation | Implemented | host harness allowlists | host report schemas | host-environment-isolation tests | Fake-host canary pass | Real Provider blocked by Owner prerequisites | Keep real calls blocked until prerequisites |
| PRD-SEC-004 | Separate read/write capability; MCP is not an OS sandbox | Implemented | read/sink process split | MCP/sink schemas | tool-list/grant tests | Local stdio | Real OS-user/container proof absent | Verify least privilege in each real Host |
| PRD-SEC-005 | Exclude client/case/private/unauthorized material | Implemented | sink/legal gates | confirmation/sensitivity contracts | case-boundary/security tests | Local only | No external corpus audit | Human-review all future Gold manifests |
| PRD-SEC-006 | Poisoning, leakage, scope escape, unauthorized mutation are release blockers | Implemented | KA/KMCP/sink gates | security hard-failure contracts | autonomy safety/security tests | Local protocol only | External security track incomplete | Retain hard failures; do not average |
| PRD-SEC-007 | Origin and influence remain non-malleable through transforms | Implemented | KA consolidation/admission | provenance/Knowledge schemas | consolidation/authority tests | Local only | Full lifecycle adversarial holdout absent | Freeze summarize/consolidate/echo attacks |
| PRD-SEC-008 | Cross-project/Vault/worktree/task deny by default | Target | physical Vault + scope + exact task-line admission | task binding v1 plus existing Vault/scope contracts | task binding/lineage/worktree/Vault regressions | Same-Vault wrong-line remediated in development; default physical cross-Vault leak not reproduced | CWD covered; real Host/embedding/explicit cross-Vault matrix remain | Freeze remaining cross-boundary cases |

## Operations and portability mapping

| PRD ID | Capability | Current status | Runtime code | Contract/Schema | Tests | External evidence | Limitation | Required next action |
|---|---|---|---|---|---|---|---|---|
| PRD-OPS-001 | One recoverable coordinator for canonical mutations | Implemented | KA, KC | sink/compilation/revision schemas | coordinator/recovery tests | Local only | Future UI must reuse it | Reject parallel mutation logic |
| PRD-OPS-002 | Verifiable backup of canonical state, capability, and recovery data | Implemented | KS, KA snapshot | backup/snapshot contracts | migration/snapshot tests | Local only | Owner-only snapshot intentionally includes sink capability token material; Provider/API secrets are excluded; it is never a release artifact | Run owner-only restore qualification |
| PRD-OPS-003 | Derived indexes/views can be deleted and rebuilt | Implemented | CS, WIKI, intelligence | derived manifest v2 | rebuild/equivalence tests | Local synthetic | Current-candidate 3-OS/scale absent | Qualify after product Gold passes |
| PRD-OPS-004 | Recover post-commit/pre-materialization failure | Implemented | KA pending materialization/recover | recovery event contracts | projection/autonomy recovery tests | Local fault injection | Real crash/process interruption pending | Run interrupted mutation task |
| PRD-OPS-005 | OKF/AKBP are interchange only | Deferred | No v0.13 runtime | None | None | Research-only UP | No admitted interoperability failure | Route to v0.14/v0.15 research |
| PRD-OPS-006 | Exports bind exact hashes and exclude secrets/paths | Target | partial package/export code | package v1/manifests | package/security tests | Historical local artifacts only | Fresh current wheel/provenance/redownload absent | Freeze export disclosure and round-trip task |
| PRD-OPS-007 | Forget/erasure updates eligibility and projections without tombstone leakage | Implemented | KA forget/GC, WIKI rebuild | lifecycle/tombstone contracts | forget/GC/rebuild tests | Local only | Independent selective-forget Gold absent | Run write-execute-forget holdout |
| PRD-OPS-008 | Semantic restore creates a new attributable revision/event | Not Implemented | Snapshot/Vault rollback only | No public semantic-restore contract | No complete revision-restore acceptance | None | Pointer rewind is forbidden and not a substitute | Reproduce user rollback task before design |
| PRD-OPS-009 | Bounded integrity-verifiable operational and Run Timeline | Target | events/run records/query trace primitives | separate existing contracts | `test_prd12_run_timeline_reproduction.py` | `reproduced_missing_public_seam` | No unified owner timeline, filtering, pagination, forget, or restore events | Freeze owner time-to-locate and lifecycle Gold |

## Capability disposition

| Capability | Current | Qualification boundary |
|---|---|---|
| Continuity/Context | Target workflow with implemented route/snapshot/admission kernel | Semantic cold-start, density, fork lifecycle, Human Gold, native-memory comparison, and real Hosts pending |
| Living Wiki | Implemented development chain | Independent human usability, ownership classes, typed path task, and current scale pending |
| Protected/Legal Evidence | Implemented runtime, failed development qualification | Exact signed/verified Pack, independent legal Gold, and temporal/exception primary evidence pending |
| Host Integration | Target with local static/thin-adapter evidence | Real isolated Codex/Claude/OpenCode runs and secret preflight pending |
| Portability/Operations | Target with local primitives | Timeline, semantic restore, fresh artifacts, 3 OS, reproducibility, SBOM/provenance, and public redownload pending |

## Pass 5 historical addendum

Pass 5 does not upgrade any PRD row to `Qualified` or `Released`.

- Wiki wrong-merge, alias-collision and typed cycle cases now execute through public mutation,
  projection, Resolver and graph seams. They remain repository-visible development evidence;
  independent human Wiki task qualification is `not_executed`.
- Statement 5,001 now executes through public Source Revision and semantic compilation stages with
  Source/Compilation/Knowledge Revision inventory digests. Statement 10k/100k and Relation
  500/5000/10k/100k remain required `not_executed` lanes.
- A `limit=1` public graph probe reproduced missing selection-truncation Gap/Receipt evidence. It is
  a current correctness blocker and was not hidden by raising/removing the bound.
- The fresh Query v6 challenge was consumed during remediation and is now
  `development_tuning_used`; it cannot be Human Gold, qualification holdout or final blind.
- Product-outcome package v1 is benchmark-only, rejects every pass, and leaves assembly disabled.
  Human Gold, exact Legal Pack, real Codex, performance, Platform Core and public-redownload gates
  remained unexecuted or failed as recorded in the historical
  `docs/V0_13_PASS5_DISPOSITION.md`.

## Pass 5 gate classification and skip disposition

| Gate class | Rule | Pass 5 status |
|---|---|---|
| **Core** | Required; no core safety, integrity, legal, boundary, scale, platform, or supply-chain gate may be lowered | Kernel correction does not lower a Core gate; required external evidence remains `not_executed` |
| **Capability** | May remain `not_claimed` when the capability is not declared | Run Timeline and semantic restore remain deferred; Claude/OpenCode remain `not_claimed` unless support is explicitly declared; E2E continuity remains `Target` |
| **Competitive Claim** | Independent named-comparator/host evidence; kernel evidence cannot satisfy it | Independent gate remains false and `not_executed`; no superiority/SOTA claim |

The remaining six pytest skip dispositions are explicit non-results, not passes or silent
omissions. The three prior Wiki skips now execute but remain development-only:

| Required lane | Disposition |
|---|---|
| Statement scale 10k | `required not_executed` |
| Statement scale 100k | `required not_executed` |
| Relation truncation 500/5000 | `required not_executed` |
| Wiki wrong merge | `development executed`; independent human task `not_executed` |
| Wiki alias collision | `development executed`; independent human task `not_executed` |
| Wiki cycle | `development executed`; scale truncation `not_executed` |
| Historical v0.6 wheel | `separate compatibility not_executed` |
| Windows native ACL | `macOS not_applicable`; Windows evidence remains required |
| Windows native junction | `macOS not_applicable`; Windows evidence remains required |

## Current source-candidate status pointer

Pass 1-8 reports remain immutable historical development evidence. The current evidence boundary
is recorded in `docs/V0_13_PASS10_CURRENT_DISPOSITION.md`. Pass 10's b14 Statement, Codex, and
Obsidian artifacts are historical candidate evidence: the Statement report fails the current Gold
byte binding, the Codex environment receipt fails the current child-argv contract, and the Codex
candidate prompt exposed evaluator labels, the expected marker, and an exact Knowledge ID. Those
artifacts cannot be promoted by rewriting commit, tree, or hash fields. Repository-visible
development Gold and Context reports remain tuning-used, not independent holdout evidence. Human
Gold, physically isolated qualification/final blind, exact Legal Pack, uncontaminated exact-head
Codex/OpenCode tasks, required Wiki/Relation scale, final cross-platform artifact chain, signature,
and public redownload remain `not_executed` or unresolved.

Pass 11 has added local fail-closed coverage for candidate/evaluator file separation, neutral Host
output, natural-task discovery without an exact Knowledge ID, wrong-route exclusion, stale-revision
exclusion, and `workspace_diverged`. Three exact-wheel Codex App Server A/B/C/D workflows executed
as claim-ineligible candidate evidence. None passed all four conditions: the full 19-operation C
condition failed in every attempt and exact MCP D failed in every attempt. Only attempt 1 B was
evaluator-scoreable, with First Correct Action `0.0`. The multi-state continuity suite, independent
Human Gold, physical qualification/final-blind split, and remaining Host matrix are not qualified.

Current disposition remains:

```text
release_gate_passed=false
claim_eligible=false
competitive_claim_eligible=false
package_version=0.12.0
source_candidate_remains_not_released
```
