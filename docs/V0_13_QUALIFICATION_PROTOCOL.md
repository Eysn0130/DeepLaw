# DeepLaw v0.13 Qualification Protocol

Status: **v3 Kernel-release protocol frozen; exact candidate binding pending**
Reviewed: **2026-08-21**

Package and main remain `0.12.0 Beta`. The active record is
[`benchmarks/v013/active-qualification-v3.json`](../benchmarks/v013/active-qualification-v3.json):
`status=machine_evaluation_pending`, `profile=kernel_release_core`,
`release_ready=false`, and `claim_eligible=false`. The current classification is Gate v9. This
protocol does not authorize a `0.13.0` tag/release, RC, GA, Human Gold, legal attestation, or a
competitive claim.

The machine-readable protocol is
`contracts/v013-qualification-protocol.v3.schema.json`; the frozen bytes are
`benchmarks/v013/qualification-protocol-v3.json` and its recorded SHA-256. Protocol v1-v2 and Gate
v1-v8 remain historical compatibility inputs. They are not rewritten or used as current state.
The frozen Host process receipt v1 remains a historical compatibility record and is
`invalidated-for-current-qualification`; current Gate v9 control admission requires the sibling
`deeplaw.host-process-receipt/v2`. Current Host evidence continues to use
`deeplaw.host-continuity-qualification/v2`.

## Product and Provider boundary

Qualification covers three product roles on one shared governed kernel:

1. Task Continuity / Governed Project Knowledge;
2. Source-native Evidence Library; and
3. Living Wiki.

All use the same Context Compiler:

```text
Discovery -> Admission -> Selection
  -> Bounded Verifiable Knowledge Capsule
  -> thin Codex / OpenCode / other Host drivers
```

The Context Compiler is not a fourth product or a second retrieval engine. Legal Pack is the
first-party legal policy plane of the Evidence Library. Professional source stays source-native;
the Wiki is not a complete editable canonical copy. DeepLaw does not automatically ingest a Host
transcript, prompt, hidden reasoning, raw log, authentication, or Secret as memory.

The current Provider advertisement is knowledge-support input v7/output v6 with only `query`,
`context`, and `explain`. Input v1-v6 and output v1-v5 are compatibility/internal. Provider output
must not contain paths, session hashes, internal selection identity, raw logs, transcript,
reasoning, Secret material, or unadmitted content. Ordinary reads must not append the canonical
Ledger.

## Why this is not a result

A protocol, validator, local regression, mock, dry-run, source-free diagnostic, caller-authored
PASS field, old report, or public synthetic fixture is not qualification evidence. A repository
file named `holdout` is still development material. Missing exact source-specific evidence remains
`not_executed`; it is not a score of zero and cannot become a pass through an empty denominator.

Machine reviewers and Luna may produce machine audit evidence. They are not independent human
review, Human Gold, legal experts, `human_verified`, or model diversity. The active profile has no
human or legal attestation.

The runner remains **diagnostic first**: a source-free diagnostic must succeed before any costly
external task is attempted, but it is never qualification evidence. Older protocol prose described
the next stage as external Human Gold; under v3, Human, legal-expert, machine-reference, blind,
panel, scorer, and arbiter evidence is outside Kernel Release Core unless its separate claim is
attempted.

## Exact candidate and invalidation

Before any formal task, candidate preparation must bind one clean prospective integration commit
whose first parent is the latest frozen main. It must update only current version surfaces to
`0.13.0` and leave the active record in the
construction state required by Candidate Full. Candidate preparation supports dry-run and fails
closed; it does not accept a dirty tree, wrong integration commit, main-branch apply, or a
Secret-bearing input. Competitive/research external inputs are not candidate-preparation
prerequisites.

Candidate Full produces exactly one reproducible wheel and one sdist and binds:

- source commit and tree;
- `uv.lock` SHA-256 and package version;
- wheel/sdist filename, byte size, and SHA-256;
- retained artifact manifest;
- SBOM, installed licenses, OpenVEX, and provenance;
- exact workflow/run identity.

Kernel Qualification Evidence and Commercial Qualification download those same artifacts and never
rebuild them. A
change to behavior code, dependency, documentation contract, commit/tree, wheel/sdist bytes, or an
external input invalidates the current qualification. A replacement candidate requires a fresh
build and fresh Candidate Full, Kernel Qualification Evidence, and Commercial Qualification runs.
The same artifact/input pair is not rerun to select a favorable model result; apart from the single
bounded transport retry permitted by the Host task contract, a failure is diagnosed, fixed, and
refrozen.

Main remains `0.12.0` throughout qualification. If main moves, the candidate is invalid. After all
gates pass, main may only fast-forward to the exact qualified commit; squash, rebase, or a new merge
commit cannot reuse prior qualification.

## Security-domain contract

Each candidate Host and its credential broker must execute across the closed Host/MCP process
boundary. The owner-only repository-external broker admits the exact Host binary and delivers a
Secret only to that Host subprocess; DeepLaw MCP receives a closed environment. Fail-before
canaries must prove that neither Secret nor ambient authentication reaches MCP, and the retained
broker source/hash plus sanitized process receipt must agree. If a
Competitive/Research claim is attempted, its reference freezer, scorer A, scorer B, and arbiter
must additionally use distinct OS-enforced security domains.

The Host isolation evidence binds the exact executable version/hash, repository-external broker
source hash/byte count/owner-only mode, successful process receipt, and negative-canary results.
Competitive security domains additionally retain process/mount/ACL/network/IPC observations. They
share no reference, scorer output, or transcript:

- the candidate cannot read sealed references, expected labels, scorer inputs, or scorer outputs;
- a scorer receives only sanitized candidate output and the sealed reference;
- the arbiter receives only the two scorer results and their bound receipts; and
- credential brokers deliver a Secret only to the exact Host process. DeepLaw runners, reference
  freezer, scorers, arbiter, and evidence assembler receive no Secret or `.env` path/content.

Failure to prove an OS-enforced Host boundary, a negative canary, or closed Secret delivery blocks
the Kernel `secret_host_isolation` gate. Missing machine-reference/scorer isolation blocks only the
corresponding Competitive/Research claim.

## Frozen minimum Kernel compatibility map

Minimum Kernel compatibility is a release acceptance requirement and has not yet been qualified.
It is derived from the following version-bound behavior tasks; there is no generic `parity` gate.

| Reference/baseline | Required in-scope Kernel behavior tasks | Existing Core gates and evidence | Explicitly outside this baseline |
| --- | --- | --- | --- |
| OpenWiki / `f078160e248f889d66ee37dc0d431854f50d3294c` | Compile a source/repository into a maintainable Wiki; prove full/incremental/no-op equivalence and user-file protection | `canonical_integrity`, `living_wiki`, `scale_performance`; public CLI/MCP receipts over the exact candidate | OpenWiki UI, provider/connector breadth, ecosystem size, or overall product comparison |
| Tolaria / `367a91416477c90bbfae766dc06add3de6ae75a7` | Read Markdown/Wikilinks; controlled edits; conflict/reconcile; source-successor identity; wrong-merge rejection | `canonical_integrity`, `living_wiki`, `source_citation_locator`; real-file receipts | Tolaria Desktop GUI, visual design, and runtime |
| Obsidian API / `cc1744324150c632416857c98964f87b1574a5fc` | Preserve Markdown, Wikilinks, aliases, backlinks/outlinks, rename/move identity, and edit/reconcile behavior | `living_wiki`; real-file receipts | Obsidian UI, Sync, marketplace, and complete Canvas UX |
| LLM Wiki / `350eec8a284e159b2e4cfd068d808cbf203a6cc5` | Agent-derived knowledge retains provenance, revision, Authority, scope, sensitivity, and Ledger state; Source Revision is never rewritten | `canonical_integrity`, `living_wiki`, `source_citation_locator` | Any named-product or superiority claim without a separately frozen comparator |
| Codex owner-supplied exact identity / `gpt-5.6-luna` / `reasoning=max` | Three distinct real tasks: Continuity, Living Wiki, and Professional Evidence | `codex`, `bounded_context`, `secret_host_isolation`, `living_wiki`, `source_citation_locator`, `selective_forget`, `timeline`; actual Provider bytes/tokens | Codex runtime ownership, UI, marketplace, or static/no-model smoke |
| OpenCode owner-supplied exact identity / `deepseek/deepseek-v4-flash` | The same three real tasks with independently isolated Secret handling | `opencode` and the same shared Core duties; actual Provider bytes/tokens | OpenCode runtime, UI, ecosystem, or config-only smoke |

Before every mapped task and Core gate passes, the only permitted statement is:

> Minimum Kernel compatibility is a release acceptance requirement and has not yet been qualified.

After every mapped task and Core gate passes on the exact artifact, the permitted technical claim
is bounded to:

> DeepLaw meets the frozen v0.13 Kernel compatibility baseline defined by the qualification protocol.

Neither statement permits a claim that DeepLaw equals or exceeds a whole comparator product, or
that it is perfect, SOTA, leading, fully verified, RC, or GA.

## Frozen real task set

Before any real Host execution, the repository builds one path-free external-collector handoff
with exactly six slots in frozen order: Codex/OpenCode crossed with Continuity, Living Wiki, and
Professional Evidence. It binds the complete canonical frozen task-catalog descriptor; each slot binds the
catalog's domain-seed seam descriptor, its Host-specific `native_host_task_v3` driver descriptor
including the frozen argv/plugin policy, the owner-external per-Host identity digest
and exact identity-source digest, the six typed-source slots, and the two control-receipt slots.
The handoff is a pre-execution input summary, never Host evidence: every slot and the top-level
record remain literal `status=not_executed`, `executed=false`, `claim_eligible=false`, and
`release_ready=false`. It contains no seed or prompt payload, Provider body, path, command,
environment, authentication or Secret material, transcript, reasoning, stdout, or stderr. The
external collector consumes this handoff and produces evidence separately; a handoff cannot close
any gate or change the active/package/native/typed-v3/bundle contracts.

### Host continuity

Codex requires exactly three distinct runs using the exact executable identity from the
repository-external owner input, requested model `gpt-5.6-luna`, and `reasoning=max`.
Authentication uses the supported official existing-login seam without reading, copying, printing,
or retaining auth. The receipt records the actual returned model identity and date; the selector is
not represented as an immutable model snapshot.

OpenCode requires exactly three distinct runs using the exact executable, source, and package
identity from that same external-input family, selector `deepseek/deepseek-v4-flash`, and expected
response model `deepseek-v4-flash`. Its owner-only `.env` is read only by the credential broker.
Exact executable/package hashes are external qualification inputs and sanitized receipt fields;
DeepLaw runners receive no Secret. The exact identity source SHA is retained and compared at bundle
validation, so replacing version, SHA, Host, run, or candidate bindings fails closed.

The current Gate v9 OpenCode task prefix is non-pure and frozen as
`["opencode", "run", "--format", "json"]`. It uses exactly one exact candidate project plugin;
the source and installed plugin bytes and hashes must agree, and ambient project plugins are
forbidden. OpenCode `--pure` disables the external plugin path required for native plugin-hook
evidence, so it is not part of this runner contract. This policy correction does not execute a
Host task: current Gate statuses remain literal `not_executed`.

Across the six runs, the task set covers new thread, ordinary resume without task handle,
fork/child task, compaction, concurrent worktree, stale checkpoint, workspace divergence, wrong
task line, selective forget, and no-binding/ambiguous-binding Gap. The public journey is
`init/doctor -> task start -> task locate -> task-neutral host connect -> explicit session bind ->
new thread/resume -> fork -> compaction -> stale/wrong challenges -> selective forget`.

Every Host task receipt records First Correct Action, Decision Preservation, Wrong-State
Admission, the required duty or unresolved Gap, Provider bytes, actual native Provider tokens,
selected identities, duplicate/distractor evidence, and no-hidden-mutation status. Local Query
Trace and Ledger receipts remain outside the Provider Capsule.

Each of the six task runs also retains two separate, sanitized control records. The
`deeplaw.host-preflight-receipt/v1` record contains only the closed preflight stage and reason
code, exact Host binary version/hash, and repository-external broker source hash/byte count/mode.
The frozen `deeplaw.host-process-receipt/v1` shape and validator remain unchanged for historical
compatibility, but v1 cannot occupy a current formal `host_process_receipt` slot. The current
`deeplaw.host-process-receipt/v2` record additionally binds the exact candidate and run IDs,
declared owner-external broker source and observation bindings, exact process identity distinct from
Host identity, one-time nonce, bounded issued/expiry/reference interval, and the existing
native-event aggregate digests. Codex v2 requires one exact initialized stdio connection correlated
to the real Hook session on that process. OpenCode v2 requires the per-Host broker's direct
observation of the actual public fork POST, its parent/child response identities, and the subsequent
child plugin event. Runner-constructed fork objects, fixed request digests, caller-only parent
values, PID, thread digests, or broker nonces cannot become Host identity or satisfy v2 admission.
Duplicate, replayed, expired, future-issued, cross-candidate, cross-run, cross-task, cross-process,
cross-connection, and cross-session records fail closed.

The bound validation reference time makes an admitted historical bundle reproducible without
silently disabling expiry checks. Neither control record permits a command, environment, path,
PID, stdout/stderr, raw identity, Provider body, prompt, transcript, hidden reasoning,
authentication material, or Secret. A development diagnostic, runner-side observation, v1 record,
or untyped process record is not current qualification evidence.

The Codex runner now has one public, path-free
`deeplaw.codex-owner-external-broker-control/v2` challenge/response IPC seam. Before candidate
preparation it reopens the frozen candidate commit/tree/lock/wheel/sdist bytes, the owner-only
repository-external exact broker source, Codex binary, and Host identity. It creates a fresh
60-second nonce challenge bound to the task and evidence/qualification run IDs. The exact broker
subprocess must directly return one transient v2 object showing only
`initialize -> initialized -> thread/start -> SessionStart -> shutdown`, exactly one initialized
stdio connection, the same exact Host process and connection, and a real Hook session identity
distinct from the native session digest. Any `turn/start`, model inventory or invocation, Provider
request, stderr, duplicate JSON key, missing/expired/replayed nonce, or cross-bound value fails
closed. Combined broker stdout/stderr is capped at 256 KiB while the process is running; overflow
immediately terminates the isolated broker process group, clears both buffers, and returns only a
typed failure. The exact owner-only broker source is read through an unchanged stable FD, copied to
a private non-writable execution path, and run from that copy, so replacing the inspected source
path cannot substitute executable bytes. Candidate manifest/wheel/sdist and the Codex binary are
likewise checked as repository-external, non-symlink-parent, regular single-link files with stable
FD/stat identity; the repository `uv.lock` uses the same stable read under the clean HEAD/tree
binding. The broker response is validated in memory and discarded; it is not uploaded, retained,
or placed in a formal receipt slot. The Kernel workflow uses its current evidence run ID as the
transient preflight qualification-run challenge because no future Commercial run may be predicted;
that ephemeral binding can never be reused as a formal receipt.

The OpenCode runner now has the parallel path-free
`deeplaw.opencode-owner-external-broker-control/v2` consumer. Its exact owner-only external broker,
not the repository runner, exclusively starts and supervises the pinned OpenCode 1.18.16 Host,
private loopback upstream, reverse proxy, and fresh plugin-event sink. The closed allowlist is only
`GET /global/health` -> `POST /session {}` -> `POST /session/:parent/fork {}`, with an optional final
`GET /session/:child`. Message, prompt, command, shell, summarize, init, revert, Provider, model,
MCP, share, remote-workspace-forwarding, ambient-plugin, or any other route or invocation fails
closed. Fork selects no Provider or model. The parent identity comes only from the actual public
fork ingress path: pinned upstream `Session.fork` does not put `parentID` into `createNext`, so a
response or plugin event cannot supply or repair parent lineage. The child returned by the HTTP
response must equal the child in exactly one subsequent `session.created` plugin event on the same
supervised process.

Before that broker may execute, the runner binds the external OpenCode package through an unchanged
stable FD to the frozen Host identity's package digest, exact version `1.18.16`, and source commit
`a3647eb025c7615159d417dcc49fc39fdaeba65b`. The package must be a bounded,
repository-external, non-symlink-parent, regular single-link file. This static check reads no
dotenv, auth store, credential, or Secret and cannot start the Host.
The runner likewise checks the OpenCode selector and exact target bytes without executing
`opencode --version` or any other Host command: one selector symlink is allowed, while a parent
symlink, symlink chain, non-regular, linked, non-executable, repository-internal, changed, or
hash-mismatched target fails closed. Only the exact external broker may start or supervise the Host.

OpenCode invokes the async plugin hook without awaiting its sink write. The broker must therefore
hold the fork response at its reverse proxy until that matching child event satisfies a bounded
30-second barrier; timeout, early response release, missing/duplicate/wrong event, mismatched child,
or cross-process observation fails closed. The runner sends only a 60-second candidate/run/Host/
broker/nonce challenge, consumes one strict response capped at 256 KiB combined stdout/stderr, and
discards its raw bytes. It requires the existing `deeplaw.host-process-receipt/v2` family with the
exact `{}` request-body digest and actual-route child-event proof. It neither creates nor repairs the
receipt, route digest, parent, child, process identity, PID, nonce, handoff, or correlation digest.
The external broker source is reopened through an unchanged stable FD, copied to a private
non-writable path, and executed with a closed control environment. The Kernel workflow runs this
transient preflight without the OpenCode dotenv, model selector, Provider inventory, or formal
receipt output.

Passing either zero-model capability preflight only allows that Host's formal runner to move beyond
its previous fail-before. It does not admit a Host task, prove the later model-bearing process, or
close any Core Gate. Both diagnostic modes remain fail-before. Each of the six actual task processes
must still return its own owner-external v2 receipt in the existing slot.
The v2, consumer, and Kernel validators establish only closed structure and exact cross-binding to
the candidate, run IDs, retained per-Host broker source, Host identity, native-event digests, nonce,
and time window. They cannot self-attest observation provenance; formal authority additionally
requires the exact external broker process and formal workflow provenance. The six existing Kernel
`host_process_receipt` slots remain the only receipt inventory; Kernel and Commercial reopen their
exact bytes and cross-bindings. No second external receipt directory is created or uploaded.
Focused contract acceptance is not formal Host evidence, and Codex x3/OpenCode x3 remains
`not_executed`.

### Living Wiki

The retained real tasks cover alias/same-name identity, rename/move, external edit/reconcile,
backlink/outlink, source successor, wrong merge, protected/user-owned file protection,
full/incremental/no-op equivalence, Wiki-to-exact-Source drill-down, and the retained physical
profile at exactly 10k active governed objects. Scale receipts inventory the actual artifact
families and do not generalize one
file profile to every Statement, Relation, or Wiki layout.

### Professional Evidence and optional Legal Pack capability

Kernel professional-evidence tasks bind exact source bytes for PDF, DOCX, HTML, and Markdown; Document, Version,
Fragment, Locator, quote, effective date, exception/proviso/cross-reference, OCR critical token,
wrong version/false Authority, acceptable Gap, and exact Source drill-down. The Core task also
requires Wiki-to-exact-Source drill-down and source-byte/hash preservation. An exact signed official
Legal Pack is a separate Capability gate. Its absence does not block the generic Kernel and must
remain `not_executed`/unclaimed. Agent interpretation remains `legal_authority=false`; the
machine-only profile does not claim Human or legal-expert review.

### Context

Every task freezes `expected_include`, `expected_exclude`, required duty, acceptable Gap, and hard
failures before results are opened. Review occurs at three layers: Provider Capsule, local Query
Trace/receipt, and canonical Ledger. A Provider failure cannot be repaired by a local-only field;
ordinary read must leave the Ledger head unchanged.

## Metrics and hard failures

Retained observations derive Recall, Precision, MRR, nDCG, Useful Context Recall,
RelevantChars/ContextChars, Redundancy, False Suppression, Duty Coverage, Duplicate Evidence,
Distractor Answer Delta, actual native Provider tokens, provider bytes, latency, RSS, and storage.
Provider usage is taken from the native Host response; a UTF-8 proxy or caller-authored value is not
actual token evidence.

False Authority, wrong-version primary evidence, invalid quote/locator, Secret/path/transcript
disclosure, unauthorized mutation, restricted or unadmitted output, wrong tool/parameter,
wrong-state admission, and provider overflow have maximum allowed count zero. Contradiction,
exception, temporal uncertainty, and an acceptable Gap are evidence duties rather than retrieval
noise.

## Gate v9 and execution order

All 13 Kernel Release Core gates are required:

1. `canonical_integrity`
2. `migration_recovery`
3. `secret_host_isolation`
4. `bounded_context`
5. `source_citation_locator`
6. `living_wiki`
7. `scale_performance`
8. `supported_platforms`
9. `reproducible_supply_chain`
10. `codex`
11. `opencode`
12. `selective_forget`
13. `timeline`

Gate v9 stays `assembly_enabled=false` with `awaiting_all_core_gate_pass` until source-specific
validators reopen every retained input and derive zero hard failures. Validator availability is a
code property, not evidence. `official_legal_pack`, `semantic_restore`, `claude`, and
`gui_desktop_interoperability` are Capability gates. Machine-reference isolation, comparative
blind holdouts, review panels, scorer A/B, arbitration, incremental benefit, superiority, and SOTA
are Competitive/Research Claim gates. Missing optional evidence remains `not_executed` with its
claim false and cannot block Kernel release.

The v0.13 scale gate executes exactly 10,000 active governed Knowledge Objects per Vault. Before
the measured lane, the public retrieval query and context compile each run exactly one warmup;
the report retains each warmup's elapsed time, one-sample count, exclusion marker, and Provider
payload bytes. Warmup values are excluded from exactly 30 measured query/context samples. The
measured lane reports p50/p95/max and applies hard ceilings of p95 <= 2,000 ms and max <= 5,000 ms
per surface; the query/context worst case is the typed Gate metric. It also proves RSS, storage,
file count, build/rebuild duration, full/incremental/no-op equivalence, user-byte protection, and
the Provider hard bound. More than 10,000 is experimental; 100,000 sharding/bundling belongs to
v0.14 and is not a v0.13 Core gate.

Formal order is:

1. Candidate Full: exact 0.13.0 commit, reproducible wheel/sdist, exact-wheel journey, exact 10k,
   required Python/3-OS matrix, SBOM/licenses/OpenVEX/provenance.
2. Kernel Qualification Evidence: download the same artifact, run isolated
   Host/Evidence/Wiki/Context tasks through the exact owner-controlled external collector, retain
   the no-Secret broker sources, and upload only sanitized evidence. The collector and both brokers
   are repository-external, owner-only, exact-hash inputs; their presence is a prerequisite, not
   product runtime.
3. Commercial Qualification: download the same artifact, reopen every source, and derive all 13
   Core gates plus explicit optional-claim statuses, `assembly_enabled`, `release_ready`, and
   bounded Kernel technical claims.

## Release boundary

If any Core gate fails, an external input is missing, isolation is not provable, main moved, the
artifact changed, or Owner confirmation is absent, main remains `0.12.0`; no tag, signing, or
release occurs. A passing qualification permits only a fast-forward to the exact candidate commit.
The `v0.13.0` tag and release require a separate final exact Owner confirmation and must point to
that same commit.

The release classifier remains Beta. The only allowed release name is
`DeepLaw 0.13.0 Beta — machine-evaluated technical release`, with explicit no-human/no-legal
attestation. Public redownload must reproduce the wheel/sdist SHA-256 and provenance bindings.

Historical `V0_13_PASS*.md` documents remain immutable evidence snapshots and are not current
protocol, status, or release authority.
