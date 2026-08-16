# DeepLaw v0.13 Source-Candidate Qualification Protocol

Status: **protocol frozen; external qualification candidate binding pending** (2026-08-13). The
local reproducible source-candidate package is recorded separately, package version remains
`0.12.0`, and this document does not authorize a release, RC, GA, or a competitive claim.

The machine-readable contract is
`contracts/v013-qualification-protocol.v1.schema.json`. The frozen candidate protocol is
`benchmarks/v013/qualification-protocol-v1.json`; its exact bytes are bound by
`benchmarks/v013/qualification-protocol-v1.sha256`. The sidecar hash is calculated over the
JSON bytes as stored, including its final newline.

## Why this is not a result

The protocol fixes the measurement design and the failure rules before any final result is read.
It deliberately has no external qualification candidate, holdout, Gold, scorer, host receipt, or
result hash bound yet. A clean local wheel was constructed and hash-bound in
`V0_13_PLATFORM_ARTIFACT_QUALIFICATION_REPORT.md`, but it was not run against a qualification or
final-blind holdout and therefore is not written into the protocol as if that external binding had
occurred. Every metric and external gate remains `not_executed`; this is not a score of zero and
cannot be converted into a pass by an empty denominator.

The repository-visible v0.13 fixtures are development material. A repository fixture, including a
fixture whose filename says `holdout`, is not a qualification holdout or a final blind holdout under
this protocol. A holdout used for diagnosis, tuning, or repair is automatically downgraded to the
development layer.

## Pass 21 active candidate and evidence assembly

The frozen protocol JSON above remains byte-for-byte historical. Pass 21 adds the separate active
candidate contract `deeplaw.v013-active-qualification/v1`, whose tracked construction document is
`benchmarks/v013/active-qualification-v1.json`. The generic schema accepts bounded pure semver; an
externally materialized `frozen_exact_candidate`, however, is admitted only for exact `0.13.0` and
must bind all of the following before any real qualification execution:

- clean source commit, tree, `uv.lock` SHA-256, and `SOURCE_DATE_EPOCH`;
- the single reproducibly verified wheel and sdist names and SHA-256 values;
- the retained artifact manifest bytes;
- repository-external Human Gold, qualification holdout, final-blind holdout, and
  compiler/scorer-isolation manifest SHA-256 values;
- Codex `0.147.0-alpha.1.2` / `gpt-5.6-luna` / `reasoning=max` and OpenCode `1.18.16` /
  `deepseek/deepseek-v4-flash`.

The tracked document remains `candidate_version=0.12.0`, `status=construction_candidate`, and
`blocker=release_version_binding_deadlock`. This is deliberate: package version may change to
`0.13.0` only after behavior, dependencies, Platform Core v2, and external input hashes are final.
The reproducible build then materializes the frozen active binding outside the source tree, which
avoids an artifact-hash/source-tree circular dependency. Changing the package version earlier, or
using `0.12.0` evidence for a later `0.13.0` package, is forbidden.

Gate classification v6 preserves v1-v5 and makes Timeline a required Core Gate. Mechanical Gates
use the exact development source binding and do not pretend to need a blind corpus. Human, Host,
Legal, Context, and other semantic Gates require the external qualification or final-blind layer.
Generic raw evidence is development-diagnostic only and cannot pass a Core Gate. Core validators
reopen a source-specific envelope plus retained JUnit, Platform inventory, reproducible-artifact,
Host, Human Gold/scorer, Legal exact-source, scale, or Timeline evidence; validate exact candidate,
protocol, Gold, corpus, isolation, and Host bindings; and derive executions, metrics, and hard
failures without accepting caller-authored pass/exit/metric fields. CI JUnit must contain the
Gate-specific public-seam inventory, and every Platform cell must match Platform Core v2 exactly.
One retained source may be referenced by multiple independent validators. The collection assembler
reopens every Core result and recursively reruns its source validator before enabling a decision.

Validator availability is a code property, not a qualification result. Gate v6 keeps
`assembly_enabled=false` with `awaiting_all_core_gate_pass`; the assembler cannot pre-enable a
release and derives eligibility only after reopening a complete reproducible all-Core collection.
While the tracked active candidate is not frozen, all Core Gate executions remain `not_executed`,
and no empty or skeleton collection is produced.

The checked-in `External Qualification Evidence` workflow consumes the unique successful
`Candidate Full` artifact on an owner-controlled self-hosted macOS qualification runner. It never
rebuilds the distribution and uploads only the bounded, path-free evidence bundle after recursive
source validation. Missing runner labels, external Gold/holdout mounts, or an executable independent
scorer fail the workflow; they do not create placeholder execution evidence.

## Frozen minimum Kernel compatibility map

Minimum Kernel compatibility parity is a release acceptance requirement and has not yet been
qualified. It is derived only from the following frozen, version-bound behavior tasks and their
mapped existing Core gates; there is no generic `parity` gate.

| Reference/baseline | Required in-scope Kernel behavior tasks | Existing Core gates and evidence | Explicitly outside this baseline |
| --- | --- | --- | --- |
| OpenWiki released v0.3.1 / `630eb9ec3fa22a4bed2d347fc3ea3a6a3bd22abc` (peeled commit) | Compile a source/repository into a maintainable Wiki; prove full/incremental equivalence, idempotent update, and user-file protection | `canonical_integrity`, `migration_recovery`, `scale_performance`, `supported_platforms`; public CLI/MCP receipts over the exact candidate | OpenWiki UI, provider/connector breadth, ecosystem size, or overall product comparison |
| Tolaria `v2026-08-11` / `cb45f26649a7500e0bdb5dd0b8f0412e9c1daf4d` | Read Markdown/Wikilinks; perform controlled edits; detect conflict and reconcile; preserve source-successor identity and reject a wrong merge | `canonical_integrity`, `migration_recovery`, `source_citation_locator`, `secret_host_isolation`; real-file/editor receipts | Tolaria Desktop GUI, visual design, and its Tauri/React runtime |
| Obsidian official public format/API help, accessed 2026-08-11; API snapshot `obsidian@1.13.2` / `cc1744324150c632416857c98964f87b1574a5fc` | Preserve Markdown, Wikilinks, aliases, backlinks/outlinks, rename/move identity, and edit/reconcile behavior on real files | `canonical_integrity`, `migration_recovery`, `source_citation_locator`, `human_gold_isolation`; real-file/editor receipts | Obsidian UI, commercial Sync, plugin marketplace, and complete Canvas UX |
| LLM Wiki behavior category (not a comparator project) | An Agent can generate and update knowledge while provenance, revision, Authority, scope, sensitivity, and Ledger state remain explicit; the original Source Revision is never rewritten | `canonical_integrity`, `secret_host_isolation`, `source_citation_locator`, `human_gold_isolation` | Any named-product or superiority statement unless an exact project and version are separately frozen |
| Codex `0.147.0-alpha.1.2` / `gpt-5.6-luna` | Three real task families: cold/new; resume/fork/concurrent-worktree; compaction/forget, including stale-checkpoint and wrong-task-line rejection | `codex`, `bounded_context`, `secret_host_isolation`, `selective_forget`; First Correct Action, Decision Preservation, Wrong-State Admission, and actual Provider bytes/tokens from `deeplaw.host-continuity-qualification/v2` | Codex Agent Runtime ownership, UI, marketplace, or a static/no-model Host smoke |
| OpenCode `1.18.16` / `deepseek/deepseek-v4-flash` | The same three real Host task families and wrong-state challenges as Codex, under independently isolated configuration and Secret handling | `opencode`, `bounded_context`, `secret_host_isolation`, `selective_forget`; the same outcome metrics and actual Provider bytes/tokens through the shared Host continuity contract | OpenCode runtime, UI, ecosystem, or a projection/configuration-only smoke |

The Codex and OpenCode Host task mechanics may use different public lifecycle methods, but they
must cover the same three behavior families. A static configuration check, synthetic test,
deterministic/no-model smoke, or provider-usage estimate cannot satisfy a real-Host task. Native
Host evidence may execute on the Owner-authorized machine; it neither replaces nor requires reuse
of the same credential for the separate Linux/macOS/Windows artifact qualification gate.

Before every mapped task and mapped Core gate passes, the only permitted statement is:

> Minimum Kernel compatibility parity is a release acceptance requirement and has not yet been qualified.

After every mapped task and mapped Core gate passes on the frozen candidate, the only permitted
positive statement is:

> DeepLaw meets the frozen v0.13 Kernel compatibility baseline defined by the qualification protocol.

Neither state permits claims that DeepLaw as a whole equals or exceeds OpenWiki, Tolaria, Obsidian,
or any LLM Wiki, or that it is perfect, SOTA, leading, fully verified, or generally superior.
Competitive Claim gates remain optional and `not_claimed`; Kernel compatibility does not satisfy
them.

## Pass 14 Host preflight disposition

Pass 14 corrected the current Codex App Server boundary before any new model call. A compaction
request returns `{}` and is observed through paired `item/started` and `item/completed` events whose
item type is `contextCompaction`. Deprecated `thread/compacted` may be parsed for compatibility but
cannot prove qualification success. The Codex and OpenCode runners also share one candidate,
installed-wheel, report and retained-bundle orchestrator; Host adapters retain only protocol and
event-specific behavior.

The required real-Host sequence remains diagnostic first, then three distinct continuity tasks.
It did not start in Pass 14:

- Codex closed authentication preflight failed closed because the temporary profile did not report
  a ChatGPT login through the official CLI status seam. No authentication file or keychain item was
  read and no API-key fallback was attempted.
- No installed OpenCode binary was available for the required version/config preflight. The
  project dotenv was therefore not read.

Both Host diagnostics and both three-task qualifications are `not_executed`; no canonical Host
report, manifest or `SHA256SUMS` exists for this pass. The absence of a bundle is intentional and
must not be replaced by a skeleton report or PR text. See
[`V0_13_PASS14_DISPOSITION.md`](V0_13_PASS14_DISPOSITION.md).

Pass 16 later bound the official `opencode-ai@1.18.16` installation coordinate before any new
OpenCode model task. Active gate classification v4 records that exact version while preserving the
historical v3 bytes. This prerequisite binding is not a Host result: the three OpenCode task
families and Human Gold scoring remain `not_executed` until an independent repository-external
Human Gold is frozen. Static version, isolated configuration, login/provider presence, or model
inventory checks cannot substitute for any real Host run. See
[`V0_13_PASS16_DISPOSITION.md`](V0_13_PASS16_DISPOSITION.md) for the exact candidate and
`not_executed` gate disposition.

## Pass 17 native receipt and diagnostic boundary

`deeplaw.host-continuity-qualification/v1` is historical and
`invalidated-for-current-qualification`: its exact bytes remain unchanged, but its shared
validator reused Codex lifecycle names for OpenCode. Current Host qualification and active gate
classification v5 accept only `deeplaw.host-continuity-qualification/v2`. The v2 receipt keeps
the common semantic task family separate from the transport/request seam, native requested
operation, actually observed response or event, sanitized request/observation digests, identity
lineage, and actual Provider usage. A semantic scenario name is never native observation evidence.

The required diagnostic-first sequence now has an independent `diagnostic` mode on the same Host
runner and orchestration path. It uses the repository-visible, source-free development fixture
`benchmarks/hosts/pass17-development-diagnostic-v1.json`; it does not read Human Gold or
qualification labels and does not emit a qualification score. Every diagnostic report is fixed to
`claim_eligible=false`, `qualification_status=not_applicable`, and
`evidence_class=development_diagnostic`. Active Host gates require three distinct qualification
runs, `qualification_holdout`/`final_blind` corpus roles, and applicable status, so diagnostic
evidence cannot satisfy them.

For Codex diagnostic only, the Host may inherit the existing ChatGPT login location after an
official `codex login status` check because current Codex stores authentication under
`CODEX_HOME`. The diagnostic root is persisted only so the official resume/fork seams can be
observed, then removed through the official `thread/delete` seam; cleanup must complete.
Non-DeepLaw capabilities remain disabled, and the DeepLaw MCP child still receives the closed
allowlist environment. Qualification retains the separate owner-created closed profile and fails
before candidate preparation without its frozen external Human Gold.

One diagnostic invocation contains one development run on the existing Host engine. That run
observes new, resume, fork, and native compaction seams: Codex records its App Server response plus
the two `contextCompaction` item events, while OpenCode records CLI JSON separately from
`session.get`, `session.summarize`, and `session.messages` HTTP responses. It performs no forget
mutation; every model turn must leave the Ledger head unchanged.

Pass 17 executed one such source-free development diagnostic on each Host. Both reports are
`claim_eligible=false` and cannot satisfy a gate. The repository-external Human Gold location was
empty, so the six qualification tasks and independent blind scoring remain `not_executed`. Exact
receipt hashes, Provider bytes/tokens, tools/list bytes, and the final blocked disposition are in
[`V0_13_PASS17_DISPOSITION.md`](V0_13_PASS17_DISPOSITION.md).

Qualification mode remains fail closed: repository-external Human Gold v2 must be loaded before
candidate preparation or any Host/model start and must bind the exact task-case digest, clean
candidate commit/tree, wheel SHA-256, and Host receipt v2 digest. The blind-review and run-score v2
contracts bind the same wheel and receipt contract. These structural declarations do not prove
human authorship or independence; that provenance remains an external owner responsibility.

## Pass 19 launcher and task-binding precondition

Future Host qualification must use the production fixed-target closed MCP launcher or a runner
wrapper proven equivalent by the same canary. The DeepLaw child receives isolated
HOME/USERPROFILE/XDG/temp roots, portable bootstrap variables, explicit DeepLaw data selection and
the canonical opaque task binding only. Codex authentication, OpenCode/DeepSeek provider secrets,
`.env` contents and credential paths remain Host-only. Generated configuration and reports contain
no local Vault path, and the runtime-selected Vault must match the expected opaque Vault identity.

Every new/resume/fork/compaction read must bind the registered project, repository, stable
worktree, task line, base and dirty snapshot. A successful checkpoint is written only through the
separate owner-granted Sink at an explicit successful boundary (`record_run` then working-memory
`remember`). Query/Context and all read-MCP qualification probes must leave the Ledger unchanged.
Wrong, stale, ambiguous and forgotten state must fail closed. The no-model Pass 19 acceptance
fixture is development regression evidence only; it cannot satisfy any real-Host, Human Gold or
blind-review gate.

## Three isolated data layers

The layers are mutually exclusive and have different residency and visibility rules:

| Layer | Residency | Compiler can read | Evaluator can read | Tuning |
| --- | --- | --- | --- | --- |
| `development` | repository or public synthetic | source corpus only | development labels if needed | permitted |
| `qualification_holdout` | repository-external, hash-frozen | source corpus only | compiled output and corresponding Gold | any use downgrades it to development |
| `final_blind` | repository-external, hash-frozen and unseen | source corpus only | compiled output and corresponding Gold | only after final candidate freeze; any failure followed by repair requires a new unseen holdout |

The compiler is run from the exact candidate wheel. Its only inputs are that wheel, the selected
layer's source corpus, and an explicitly provisioned DeepLaw MCP. It cannot read the repository
source tree, Gold, scorer, expected identities, private corpora, ambient credentials, or host
global configuration. Source mounts are read-only. The evaluator is a separate read-only process
whose only inputs are the compiled output and the corresponding Gold; it cannot read the candidate
wheel, compiler process, repository source, or private material, and cannot mutate either input.

## Final freeze and binding

After remediation and local verification, the maintainer must create a fresh exact wheel and record
its filename, SHA-256, and source commit in `candidate_binding`. The external evaluator then
provides independent SHA-256 values for the qualification source/Gold and (only after the candidate
is final) the final-blind source/Gold. Binding a new candidate or editing a frozen control starts a
new protocol freeze. A failed final blind run cannot be repaired against the same blind corpus.

The protocol hash must be recalculated whenever the protocol JSON changes. Thresholds, budgets, and
hard-failure conditions are frozen before final-blind results are opened. No result may be copied
back into the protocol JSON as a silent status change.

## Frozen controls and metrics

The provider-visible payload is capped at 65,536 bytes. Statement candidates are capped at 512.
Graph traversal accepts `graph_hops` 0 through 2, with at most 500 admitted and 5,000 scanned
relations per bounded operation. The RSS check is 10,000 requests with at most 10% relative growth,
the 100k storage ceiling is 2 GiB, and the concurrency check uses eight readers. Query traces are
process-local derived state with a 900-second TTL, at most 16 entries and 1 MiB aggregate storage;
they are SHA-256 integrity checked on read, owner-deletable, and plaintext-free by default.

The metric registry freezes thresholds for:

- retrieval: Recall@K, Precision@K, Target Identity Precision, MRR, and nDCG;
- context utility: Useful Context Recall, RelevantChars/ContextChars, Redundancy Rate, False
  Suppression Rate, Duty Coverage, Duplicate Evidence Rate, Distractor-induced Answer Delta,
  Token savings, and latency;
- Living Wiki: page/link/backlink coverage, orphan and gap accuracy, freshness, incremental
  correctness, and full/incremental projection reproducibility;
- legal evidence: Document and Exact Segment Recall@K, identity precision, MRR/nDCG,
  Definition/Exception/Proviso/Cross-reference Recall, Temporal Correctness, Wrong-version
  Inclusion, Citation Validity, Correct Gap Precision/Recall, False Authority Admission,
  redundancy, and RelevantChars/ContextChars;
- security and scale: secret exposure, invalid quote/locator, wrong-version primary evidence,
  unauthorized mutation, payload bytes, statement candidates, graph bounds, 10k-request RSS, and
  eight-reader concurrency.

Contradiction, exception, temporal uncertainty, and explicit gaps are evidence duties; they are not
discardable noise. Ranking or embedding scores cannot create Authority.

## Hard failures and external gates

False Authority admission, invalid quote or locator, wrong-version primary evidence, and secret
exposure have maximum allowed count zero. Unauthorized mutation, restricted disclosure, unbounded
statement/graph scans, provider overflow, blind contamination, and query-trace secret/path
exposure are also hard failures with maximum allowed count zero. A hard failure fails the gate even
if an aggregate metric would otherwise pass.

Real Codex (three isolated runs), OpenCode/DeepSeek (three isolated runs), Human Gold scoring and
the exact signed 28-source legal pack remain `not_executed`. Local Statement/Wiki 10k/100k,
10,000-request current-RSS, eight-reader, Darwin Python 3.11/3.12/3.13 and reproducible wheel/SBOM
evidence is recorded in focused development reports, but cannot satisfy the missing large-Relation,
three-OS, provenance/public-redownload or external quality gates. No ambient credential or current
desktop session is an admissible substitute.

Until every required gate has real evidence, `quality_protocol_eligible` and
`competitive_claim_eligible` remain false and the release disposition remains
`not_released_source_candidate`.
