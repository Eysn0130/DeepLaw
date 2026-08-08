# DeepLaw PRD 1.2 traceability matrix

Status: **current source-candidate mapping**, re-audited 2026-08-09 through implementation commit
`ceaaa8e417098e92efcca064604e63945833726e`. This document adopts PRD 1.2 into the development
and acceptance process. It is not a release note, qualification result, or permission to implement
every Target.

## Frozen audit boundary

- PRD revision: `1.2`; SHA-256:
  `daa524d62471801ca79699948ebca52ab194e14adcdf0bc1d332850fd7a12fb8`.
- Upstream research SHA-256:
  `00dfab0dfed139f5d81982061a75896f29552f56a125aa83bec57f0c6a860967`.
- Branch: `codex/semantic-evidence-package-fix`; reviewed implementation commit/tree:
  `ceaaa8e417098e92efcca064604e63945833726e` /
  `e2ca644e23fc01a3026350982b4a781711f29c0d`.
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
- Current implementation baseline before this documentation rotation:
  `uv run --frozen pytest --strict-markers` → `1253 passed, 9 skipped in 328.52s`.
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

## Status and evidence rules

Only the PRD states `Target`, `Implemented`, `Qualified`, `Released`, `Deferred`, and
`Not Implemented` are used below. `Implemented` means the current runtime and public seam contain
the mapped behavior with repository regression evidence; it does not mean the behavior passed an
independent Human Gold, real-Host, exact-Pack, scale, portability, or release-artifact gate.
`Qualified` requires capability-specific external evidence, and `Released` requires a published
artifact bound to that evidence. No PRD 1.2 row is marked `Qualified` or `Released` in this audit.

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
  `DISP`: `docs/V0_13_CORE_SCOPE_DISPOSITION.md`; `UP`:
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
| PRD-PROBLEM-008 | Fail closed across Vault, project, worktree, and task lines | Target | KA, KOS, Q6, TC | task binding v1 plus existing Vault/scope contracts | PRD 1.2 binding/lineage/worktree/Vault regressions | Same-Vault wrong-line reproduced then remediated in development; default physical cross-Vault leak not reproduced | Explicit cross-Vault references and full fork/conflict lifecycle remain unqualified | Freeze external lineage and explicit cross-Vault tasks |
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
| PRD-CONT-006 | Cold thread restores through one Context seam | Target | KOS, KMCP, CAP, TR exact-task kernel | Capsule v3/provider v2 | context parity, exact unique/ambiguous cold-start regressions | Repository-visible development only | No semantic resolver, stable real-Host derivation, Human Gold, or real Host | Compare Host-only, Host Memory, Host+DeepLaw on a frozen external task set |
| PRD-CONT-007 | Complement Host memory without scraping or copying it | Implemented | adapter and Context boundaries | Agent Context v1 | cross-host context tests | Static/local-only | Host memory comparison not executed | Freeze equal-budget native-memory experiment |
| PRD-CONT-008 | Ground continuity in durable project state and artifact references | Target | KA, TC, TR kernel | Run Record/event plus task binding v1 and SINK5 | core continuity + real-worktree/reconciliation regressions | Opaque route and snapshot kernel pass development regression | Stable Host-neutral ID enrollment, real Artifact lifecycle, and independent task evidence absent | Qualify with real concurrent Hosts/worktrees |
| PRD-CONT-009 | Optional future intention, never a scheduler | Deferred | None | None | None | None | No admitted user failure; not core v0.13 | Revisit only through feature admission |
| PRD-CONT-010 | Bind Vault/project/task lineage/repo/worktree/base/dirty state | Target | KA, Q6, CAP, TC, TR kernel | route/snapshot binding v1; Query Plan v6; Capsule v3 | task routing, divergence, lineage, real-worktree regressions | Top-20 loss and silent divergence reproduced; bounded route-first kernel passes development regressions | Real Host-neutral identity derivation, fork reconciliation, independent Gold, and full scale remain absent | Run a fresh unseen concurrent-worktree holdout |
| PRD-CONT-011 | Independent concurrent/fork task-line current state and explicit conflicts | Target | Exact-line read isolation only; no merge coordinator | task binding v1 preserves optional opaque parent only | two-line regressions | Concurrent current lines no longer cross-admit in development | Fork/merge/conflict reconciliation lifecycle remains not_executed | Freeze external fork/conflict Gold before any coordinator |
| PRD-CONT-012 | Content-minimized searchable Run Timeline | Not Implemented | Run records/events are primitives only | No Timeline schema/API | `test_prd12_run_timeline_reproduction.py` | `reproduced_missing_public_seam` | No owner filtering/search/deletion surface | Freeze external time-to-locate and forget Gold |
| PRD-CONT-013 | Treat Host/session/memory references as untrusted hints | Implemented | adapter envelope/admission | Agent Context v1 | cross-host/context tests | Static adapter evidence | No real Host malicious-hint run | Include wrong Host reference in holdout |
| PRD-CONT-014 | Bounded bootstrap → drill-down → explicit Checkpoint lifecycle | Target | KMCP, KOS, TR, sink split kernel | Context/Capsule/SINK5 contracts | context, route-first, legacy reconciliation tests | Repository-visible development only | No owner UI/real Host lifecycle/Human Gold; Timeline absent | Measure provider bytes and First Correct Action end to end |

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
| PRD-CTX-014 | Capsule binds Vault/project/task-line/head/revisions/policy and detects stale head | Target | CAP, Q6, TC, TR kernel | Capsule v3/plan v6/task binding v1 | stale-runtime, route-first, snapshot-divergence tests | Local exact-route/snapshot kernel passes; stale Gap is bounded and redacted | Full changed-head re-resolution, real Host derivation, and fresh external Gold absent | Run changed-head re-resolution holdout |
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

Current disposition remains:

```text
release_gate_passed=false
claim_eligible=false
competitive_claim_eligible=false
package_version=0.12.0
source_candidate_remains_not_released
```
