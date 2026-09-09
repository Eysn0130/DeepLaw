# DeepLaw PRD 1.3.3 traceability matrix

Status: **current requirement-to-runtime mapping**
Reviewed: **2026-09-09**

This matrix maps the frozen Product Requirements to current runtime seams, contracts, tests, and
evidence boundaries. It is not a release ledger, qualification result, artifact manifest, or
permission to implement every `Target`. Runtime behavior remains authoritative in code, contracts,
migrations, and tests. The [Architecture](ARCHITECTURE.md) document is the sole current
architecture constitution.

## Current machine-state pointer

- Active gate classification: `v9`.
- Active qualification profile: `kernel_release_core`.
- Current package, exact integration commit/tree, candidate artifact hashes, external input hashes,
  and release decision are read from the active qualification records and their protocol; this
  matrix intentionally does not duplicate those mutable values.
- Compatibility and historical definitions remain readable through their versioned files. They do
  not change the current Provider advertisement or product roles.
- `Qualified` requires capability-specific retained external evidence. `Released` requires a
  published artifact bound to that evidence. This matrix does not mark a capability `Qualified` or
  `Released` merely because local tests pass.
- Kernel Release Core, Capability, and Competitive/Research Claim evidence are independent
  categories. Missing optional Capability or research evidence suppresses only that claim; it may
  not be promoted into a Kernel Core blocker. The exact inventories are owned by the versioned v9
  classification, not duplicated here.

## Frozen architecture mapping

| Frozen role or invariant | PRD location | Runtime and contract boundary | Evidence boundary |
| --- | --- | --- | --- |
| Task Continuity / Governed Project Knowledge | §1, §7.1, `PRD-CONT-*` | Existing Run, Checkpoint, task-lineage, workspace binding, Ledger, Coordinator, and owner-granted sink | Development regressions are local evidence; native Host and external task evidence remain separate |
| Source-native Evidence Library | §1, §7.2, `PRD-SRC-*`, `PRD-EVID-*` | Source Revision, CAS/Registry, Document/Version/Fragment/Locator, parser provenance, source-first drill-down | Generic professional-source Core evidence is independent of the signed official Legal Pack Capability claim |
| Living Wiki | §1, §7.4, `PRD-WIKI-*` | Governed Markdown Revisions, Page Registry, Link Index, Resolver, rebuildable projections | Full/incremental equivalence, user-file protection, scale, and human/editor tasks qualify independently |
| One shared Context Compiler | §1, §6, §7.5, `PRD-CTX-*` | Discovery → Admission → Selection → bounded Knowledge Capsule → thin Host driver | Provider bytes/usage and task outcomes must come from retained native observations |
| One governed kernel | `PRD-PRINCIPLE-003`, §6, §14 | Shared identity, provenance, Authority, scope, sensitivity, lifecycle, bitemporal Ledger, grants, CAS, recovery | No parallel product engine, database, kind, predicate, page family, or Host runtime is admitted |
| No automatic transcript memory | `PRD-CONT-002`, `PRD-CONT-005`, `PRD-CONT-007`, `PRD-CONT-013`, §13 | Bounded attributable checkpoint only; no transcript/background scrape or hidden write | Transcript, reasoning, auth, raw logs, and complete diffs remain outside provider and canonical state |

## Evidence and status rules

`Implemented` means the mapped public seam and repository regression evidence exist. It does not
mean real Host, independent Human Gold, signed Legal Pack, scale, portability, or release-artifact
qualification. `Target` means the requirement remains part of the frozen product direction but is
not yet proven at the required evidence boundary. `Deferred`, `Not Implemented`, `Qualified`, and
`Released` retain the meanings defined in PRD §15.

Evidence labels used below are repository-relative:

- `KA/KS/KC`: knowledge autonomy/store and compilation Coordinator;
- `TC/TR`: task context binding and rebuildable task-route projection;
- `Q/CAP`: Query Plan and local Knowledge Capsule assemblers;
- `KMCP/SINK`: `knowledge_support` read process and owner-granted `knowledge_sink` process;
- `WIKI`: projection, Page Registry, Link Index, Resolver, and Wiki coverage services;
- `LEGAL`: source/legal stores, evidence cards, and isolated `law_support` process;
- `ARCH/AKO`: [`ARCHITECTURE.md`](ARCHITECTURE.md) and
  [`AUTONOMOUS_KNOWLEDGE_OS.md`](AUTONOMOUS_KNOWLEDGE_OS.md);
- `PROTO`: [`EVALUATION_PROTOCOL.md`](EVALUATION_PROTOCOL.md),
  [`V0_13_QUALIFICATION_PROTOCOL.md`](V0_13_QUALIFICATION_PROTOCOL.md), and applicable external
  protocol documents.
- `UPC`: [`run_upstream_product_closure.py`](../benchmarks/v013/run_upstream_product_closure.py),
  the public-seam, source-free development closure runner. Its retained receipt is development
  evidence only; it is not Host, external benchmark, qualification, or release evidence.

## Problem and principle mapping

| PRD IDs | Frozen requirement family | Runtime/code seam | Contract and regression evidence | Current boundary |
| --- | --- | --- | --- | --- |
| `PRD-PROBLEM-001..008` | Correct handoff, minimum useful context, reusable compilation, shared Wiki, evidence/Authority separation, poisoning resistance, temporal state, and fail-closed project/task boundaries | `KA`, `KC`, `Q`, `CAP`, `TC`, `WIKI`, `LEGAL` | Core continuity, source, Wiki, security, temporal, and identity tests | Repository evidence is development-only until the corresponding external task and artifact are retained |
| `PRD-PRINCIPLE-001..004` | Minimum sufficient context, evidence before interpretation, one governed system, knowledge never grants control authority | Shared Coordinator, admission, Ledger, `KMCP`, `SINK`, `LEGAL` | Source binding, Authority, grant, no-write, and provider-projection tests | No score, link, model confidence, or retrieved instruction may create Authority or permission |
| `PRD-PRINCIPLE-005..008` | Progressive disclosure, open surface/trusted kernel, replaceable derived state, first-class negative knowledge | `WIKI`, derived manifests, `Q`, Gap/contradiction projections | Rebuild, bounded payload, contradiction, Gap, and user-file protection tests | Derived state is disposable and all unresolved evidence remains visible |
| `PRD-PRINCIPLE-009..012` | Earned complexity, stable invariants, state-trajectory correctness, newest-is-not-necessarily-relevant | Feature-admission process, lifecycle/revision/lineage services | Reconciliation, stale, fork, forget, and recovery tests | New public surface or primitive needs a real failed task, ADR, recovery/security plan, and Owner approval |

## Continuity and governed knowledge mapping

| PRD IDs | Requirement family | Runtime/code seam | Contract and regression evidence | Current boundary |
| --- | --- | --- | --- | --- |
| `PRD-CONT-001..005` | Bounded attributable Checkpoint; no full chat, reasoning, logs, credentials, grant minting, or hidden writes | `KA`, `TC`, `TR`, `SINK` | Checkpoint schema, grant/idempotency, no-write, provenance, and secret/path negative tests | Local contract evidence only; real Host lifecycle is a separate qualification input |
| `PRD-CONT-006..009` | Task-text-first continuity, Host-memory complementarity, durable project state, explicit intentions without scheduling | `TC`, `Q`, `CAP`, `KMCP` | Cold/resume, artifact binding, no transcript, and bounded capsule tests | Ordinary resume cannot require a handle; no background transcript memory |
| `PRD-CONT-010..014` | Vault/project/repository/worktree/lineage binding; fork/conflict; bounded Timeline; bootstrap/drill-down/checkpoint lifecycle | `KA`, `TC`, `TR`, `Q`, `CAP`, Coordinator | Wrong-line, stale snapshot, ambiguous route, fork, compaction, selective forget, Ledger-invariance tests, and `UPC` development receipt | Ambiguity remains a structured Gap; external First Correct Action and Decision Preservation require fresh retained tasks |
| `PRD-SRC-001..006` | Immutable Source Revision, structural/locator preservation, deterministic validation, targeted invalidation, idempotence, explicit withdrawal/forget | `KS`, `KC`, CAS/Registry, lifecycle services | Source revision, parser, successor, invalidation, idempotency, recovery tests, and `UPC` development receipt | Source bytes remain canonical; changed input creates a successor |
| `PRD-SRC-007..010` | Lossy compilation disclosure, targeted refinement, bounded acquisition, connector non-authority | `KC`, source adapters, admission, Gap/coverage services | Coverage, bounded fallback, allowlist, snapshot, provenance, and connector negative tests | No connector or Wiki projection can create identity or Authority |
| `PRD-SRC-011..012` | Source-native professional material and revision-bound OCR/layout/search accelerators | Evidence Core, Document IR, `LEGAL`, derived manifests | PDF/DOCX/HTML/Markdown, OCR-risk, exact Locator, and rebuild tests | Exact professional-source and critical-token evidence is Core; official Legal Pack status needs separate signed-pack evidence |
| `PRD-KNOW-001..004` | Revision/event writes, owner-granted sink, read-only surfaces, stable identity and explicit split/merge lineage | `KA`, Coordinator, `SINK`, `KMCP` | CAS/CAS, idempotency, relation, conflict, and no-hidden-write tests | No read path appends canonical Ledger |
| `PRD-KNOW-005..010` | Separate origin/Authority/time/scope dimensions, no ranking elevation, lineage-preserving consolidation, bounded maintenance, explicit Vault boundaries | Ledger, lifecycle, relation, scope/sensitivity admission | Authority, consolidation, duplicate, orphan, scope, and maintenance tests | Derived rankings and feedback remain discovery/ranking aids only |

## Living Wiki mapping

| PRD IDs | Requirement family | Runtime/code seam | Contract and regression evidence | Current boundary |
| --- | --- | --- | --- | --- |
| `PRD-WIKI-001..004` | Evidence-labelled pages, shared semantic links, path-independent identity, rename/move and revision reconciliation | `WIKI`, Markdown Registry, reconciliation Coordinator | Page, identity, rename, edit, and reconcile tests | Wiki is a projection; protected Source remains source-native |
| `PRD-WIKI-005..006` | Bounded registry/link lookup and full/incremental equivalence preserving user-owned files | Page Registry, Link Index, Resolver, projection builder | Cold/warm lookup, ownership, rebuild, incremental-equivalence tests, and `UPC` development receipt | The selected profile is configuration-bound; ownership, rebuild and recovery remain shared; scale and cross-platform evidence are qualification inputs, not local claims |
| `PRD-WIKI-007..009` | Optional derived navigation, editor-client boundary, attributable edits and unequal Authority | Projection/adapter surfaces, Coordinator, lifecycle | Adapter, ownership, protected-source, and no-control-plane tests | Standard v2 has no per-object/community/Canvas fan-out; full is an explicit advanced opt-in; no new page family or editor runtime is implied |
| `PRD-WIKI-010..013` | Machine-readable identity/status, typed Relation semantics, ownership classes, bounded graph/path reads | Ledger, Relation Revisions, Resolver, Wiki projections | Relation, status, path-bound, and Gap/truncation tests | Wikilinks/backlinks are navigation, not Authority or typed Relation claims |
| `PRD-WIKI-014..015` | Bounded source evidence cards/links; explicit file/storage/rebuild/lookup budgets | Evidence projection, Page Registry, derived manifests | Source drill-down, artifact-family, shard, storage, and rebuild tests | Standard v2 has no per-object/community/Canvas fan-out; the ownership manifest, rebuild/recovery path, Page Registry, Link Index and Resolver remain shared; no complete editable source transcription or unbounded per-record artifact fan-out |

The v0.13 named projection profile contract is version 2 while retaining the
`deeplaw.projection-profile/v1` schema family and closed v1 compatibility for historical inputs.
This is a default-materialization change, not a new Page Family: the Core Living Wiki is the
standard profile, while communities and all Canvas families require the explicit full profile.

## Context and delivery mapping

| PRD IDs | Requirement family | Runtime/code seam | Contract and regression evidence | Current boundary |
| --- | --- | --- | --- | --- |
| `PRD-CTX-001..004` | One recommended Context seam; admission before ranking/selection; hard item/source/character/token/payload bounds; minimum admitted output | `Q`, `CAP`, `KMCP` | Query plan, capsule, admission, budget, provider byte tests, and `UPC` development receipt | Provider output is a bounded projection, not the local audit object |
| `PRD-CTX-005..009` | Provider disclosure exclusions, bounded visible fallback, explicit Gap, local trace lifecycle, re-resolvable receipt | `Q`, `CAP`, `KMCP`, trace/receipt services | Secret/path/transcript, fallback, Gap, TTL/delete, and receipt tests | Paths, logs, reasoning, candidate text/scores, SQL, and Secret never cross the seam |
| `PRD-CTX-010..012` | Downstream task/evidence-duty quality, stateless retry, machine-readable capability discovery | Query plan, duty planner, MCP capabilities | Quality, retry, version, tool-list, and no-write tests | Retrieval metrics do not establish Authority; native Provider usage is required for qualification |
| `PRD-CTX-013..015` | Order-independent eligibility, exact input-head binding/staleness, fail-closed ambiguity | `Q`, `CAP`, `TC`, `TR`, admission | Tail/position, stale-head, changed-worktree, and ambiguity tests | Resource truncation and ambiguity remain explicit; no silent broader-scope search |

The current autonomous-core Provider advertisement is `knowledge-support.input/v8` /
`knowledge-support.output/v7` (`knowledge-support input v8/output v7`) with only `query`, `context`,
`explain`, and typed `read`. Input v7 remains unchanged; the new output schema accepts existing
operation response versions plus output/v7 for read. Older broad operation inventories are not
current advertisements.

The source candidate closes a bounded no-shell Agent read journey under `PRD-PRINCIPLE-005`,
`PRD-WIKI-014..015` and `PRD-CTX-010..012`: `progressive_read.py`, Source/Wiki services, input v9,
output v8 and `tests/test_mcp_progressive_read.py` cover current ordinary knowledge, its Wiki page
and exact source fragments. Aggregate navigation, historical exact reads and task-memory direct
reads are not included. Public stdio regression is development evidence, not native Host or release
qualification. Internal service existence alone is not product reachability.

The general v6 Query Plan reports unresolved exception duties when no adequate witness exists;
the presence of a source passage or a limitation does not prove exception completeness. This
boundary is distinct from the isolated `law_support` query implementation and its qualification.

## Evidence, security, and operations mapping

| PRD IDs | Requirement family | Runtime/code seam | Contract and regression evidence | Current boundary |
| --- | --- | --- | --- | --- |
| `PRD-EVID-001..004` | Protected-source immutability, isolated `law_support`, official/private separation, exact document/version/quote/time/receipt | `LEGAL`, Source Core, trust and federation services | Source lifecycle, professional evidence, citation, temporal, and process-isolation tests | Exact external source tasks qualify the generic Kernel; signed/verified Pack bytes are additionally required only for the official Legal Pack claim |
| `PRD-EVID-005..009` | `legal_authority=false`, zero false Authority/wrong version/invalid locator, Gap on unverifiable chain, no adjudication, source-first duties | `LEGAL`, `Q`, evidence cards, duty planner | Authority, wrong-version, locator, exception/proviso/cross-reference, OCR, and Gap tests, including `tests/test_v013_query_v6_exception_duty.py` | DeepLaw provides evidence/context only; legal applicability and verdict stay outside |
| `PRD-SEC-001..004` | Local canonical state, no implicit upload/telemetry, untrusted input, closed Host and read/write boundaries | Stores, source acquisition, launcher, `KMCP`, `SINK`, adapters | No-network, injection, environment, tool-list, grant, and no-write tests | OS enforcement and real Host isolation must be retained separately from fake-host diagnostics |
| `PRD-SEC-005..008` | Client/case exclusion, release-blocking leakage/scope/mutation failures, non-malleable origin, cross-boundary deny-by-default | Sensitivity, provenance, lifecycle, scope, and process controls | Canary, ACL/mount/IPC, origin-transform, cross-Vault/worktree/task negative tests | No credential, case data, private path, transcript, or raw secret is admissible |
| `PRD-OPS-001..004` | One Coordinator, verifiable backup, rebuildable derived state, post-commit recovery | `KA`, `KS`, `KC`, snapshot/recovery services | Coordinator, backup, migration, replay, fault-injection, rebuild-equivalence tests, and `UPC` development receipt | Canonical state remains valid while derived work is pending |
| `PRD-OPS-005..009` | Interchange-only adapters, exact-hash exports, explicit forget/erasure, no pointer-rewind semantic restore, bounded operational Timeline | Export, lifecycle, Wiki, `TC/TR`, snapshot services | Export disclosure, forget, GC, restore/recovery, timeline-bound tests | Semantic restore and external owner time-to-locate remain distinct qualification tasks |

## Six-capability 0.13 acceptance attachment

This finite development attachment supplements the original thirteen Core and six real Host
slots. It neither replaces those gates nor makes target research behavior current. A capability
is not complete merely because one of its linked regression slices passes. These finite states
refer to focused development scenarios; full source verification and exact-artifact qualification
remain separate requirements. C2 uses compiler-declared gaps where no domain oracle exists.

| Capability | PRD binding and candidate seam | Current acceptance state |
| --- | --- | --- |
| C1 complete dependency maintenance | `PRD-KNOW-009,011`; Statement v2/publication v4, Coordinator, `evidence/support.py`, `test_complete_support_sets.py` | 有限场景通过: AND/OR support, source successor, unchanged reimport, bounded unknown, concurrent-head rejection, unchanged independent revisions and rebuild/snapshot slices |
| C2 evidence duties | `PRD-EVID-004..009`, `PRD-CTX-001..012`; shared duty planner and exception/source/version regressions | 有限场景通过: current v7 exact passages, missing definition/exception, declared missing annex, unknown time, rejected wrong revision, truncation and denied-scope gaps; no semantic completeness claim |
| C3 continuity and ordinary recall | `PRD-CTX-014..016`; shared v7 kernel, task routes, ADR 0010, `test_governed_revision_recall.py`, `test_task_action_ledger.py` | 有限场景通过: ordinary recall plus four-state Run/CAS, synthetic A-to-B response-loss recovery, fork/constraint/route and failed/unknown snapshot slices; six native Host slots remain open |
| C4 accountable omissions | `PRD-CTX-013,017`; v7 selection explanation, admitted receipt v2, signed bounded pages and combined item/character/estimated-token budgets | 有限场景通过: admitted-only omissions, public denied-scope/token budgets, signed 20-item pagination, tamper rejection and shared cache/head/lifespan invalidation; discovery remains explicitly non-exhaustive |
| C5 selective repair/forget | `PRD-KNOW-007,012`, `PRD-OPS-005..009`; shared lifecycle, support admission, Wiki/rebuild and Capsule verification | 有限场景通过: transitive claim/Wiki/checkpoint withdrawal, independent support retention, exact read/capsule denial, rebuild/snapshot, denied-write invariance and process exit before/after commit with ordinary lease-expiry recovery |
| C6 subsequent-task outcomes | `PRD-KNOW-008,013`; development maintenance evaluator and typed environment-event scorer | 有限场景通过: nine fixed development cases across eight outcome families, three configurations and typed environment-event scoring; no model run or comparative superiority claim |

## Qualification and historical evidence links

Qualification is source-specific and artifact-bound. Candidate Full, external Host/Evidence/Wiki/
Context/scale/platform runs, supply-chain evidence, commercial aggregation, and any release decision
must consume one exact candidate artifact and retained observations. Caller-authored PASS values,
mocks, dry-runs, old reports, local-only token proxies, and machine/Luna review cannot become Human
Gold, legal expertise, model diversity, or release evidence.

The active protocol and machine records are linked from the [documentation index](README.md). The
immutable historical evidence files remain available for audit only:

- [Pass 19 disposition](V0_13_PASS19_DISPOSITION.md)
- [Pass 20 disposition](V0_13_PASS20_DISPOSITION.md)
- [Pass 21 disposition](V0_13_PASS21_DISPOSITION.md)

Those records are not rewritten or treated as the current architecture, product surface, gate
classification, package state, or release decision.
