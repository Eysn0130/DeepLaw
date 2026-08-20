# DeepLaw v0.13 Qualification Protocol

Status: **v2 protocol frozen; machine-only candidate binding pending**
Reviewed: **2026-08-17**

Package and main remain `0.12.0 Beta`. The active record is
[`benchmarks/v013/active-qualification-v2.json`](../benchmarks/v013/active-qualification-v2.json):
`status=machine_evaluation_pending`, `profile=machine_evaluated_no_human_attestation`,
`release_ready=false`, and `claim_eligible=false`. The current classification is Gate v8. This
protocol does not authorize a `0.13.0` tag/release, RC, GA, Human Gold, legal attestation, or a
competitive claim.

The machine-readable protocol is
`contracts/v013-qualification-protocol.v2.schema.json`; the frozen bytes are
`benchmarks/v013/qualification-protocol-v2.json` and its recorded SHA-256. Protocol v1 and Gate
v1-v7 remain historical compatibility inputs. They are not rewritten or used as current state.
The frozen v1 Host receipt is `invalidated-for-current-qualification`; current Host evidence uses
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
the next stage as external Human Gold; under the frozen v2 machine-only profile that stage is an
isolated machine-reference evaluation with no human or legal attestation.

## Exact candidate and invalidation

Before any formal task, candidate preparation must bind one clean prospective integration commit
whose first parent is the latest frozen main. It must update only current version surfaces to
`0.13.0`, verify every owner-controlled external input SHA-256, and leave the active record in the
construction state required by Candidate Full. Candidate preparation supports dry-run and fails
closed; it does not accept `0.12.0`, null external hashes, a dirty tree, an unverified path, or a
Secret-bearing input.

Candidate Full produces exactly one reproducible wheel and one sdist and binds:

- source commit and tree;
- `uv.lock` SHA-256 and package version;
- wheel/sdist filename, byte size, and SHA-256;
- retained artifact manifest;
- SBOM, installed licenses, OpenVEX, and provenance;
- exact external input hashes; and
- exact workflow/run identity.

External and Commercial Qualification download those same artifacts and never rebuild them. A
change to behavior code, dependency, documentation contract, commit/tree, wheel/sdist bytes, or an
external input invalidates the current qualification. A replacement candidate requires a fresh
build and fresh Candidate Full, External, and Commercial runs. The same artifact/input pair is not
rerun to select a favorable model result; a failure is diagnosed, fixed, and refrozen.

Main remains `0.12.0` throughout qualification. If main moves, the candidate is invalid. After all
gates pass, main may only fast-forward to the exact qualified commit; squash, rebase, or a new merge
commit cannot reuse prior qualification.

## Security-domain contract

Reference freezer, candidate Host, scorer A, scorer B, and arbiter must execute in distinct
ephemeral security domains enforced by separate runners, VMs/containers, UID/ACL/mount namespace,
or an equivalent operating-system boundary. Directory names, mode `0700`, `env -i`, distinct
process IDs, or caller-authored isolation booleans are insufficient.

The retained isolation evidence must bind executable hashes, process tree, mount/ACL policy,
network/IPC policy, domain identity, and negative-canary observations derived from retained bytes.
The domains share no filesystem, IPC, or transcript:

- the candidate cannot read sealed references, expected labels, scorer inputs, or scorer outputs;
- a scorer receives only sanitized candidate output and the sealed reference;
- the arbiter receives only the two scorer results and their bound receipts; and
- credential brokers deliver a Secret only to the exact Host process. DeepLaw runners, reference
  freezer, scorers, arbiter, and evidence assembler receive no Secret or `.env` path/content.

Failure to prove an OS-enforced domain, a negative canary, or closed Secret delivery blocks
`secret_host_isolation` and `machine_reference_isolation`.

## Frozen minimum Kernel compatibility map

Minimum Kernel compatibility is a release acceptance requirement and has not yet been qualified.
It is derived from the following version-bound behavior tasks; there is no generic `parity` gate.

| Reference/baseline | Required in-scope Kernel behavior tasks | Existing Core gates and evidence | Explicitly outside this baseline |
| --- | --- | --- | --- |
| OpenWiki released v0.3.1 / `630eb9ec3fa22a4bed2d347fc3ea3a6a3bd22abc` (peeled commit) | Compile a source/repository into a maintainable Wiki; prove full/incremental equivalence, idempotent update, and user-file protection | `canonical_integrity`, `migration_recovery`, `scale_performance`, `supported_platforms`; public CLI/MCP receipts over the exact candidate | OpenWiki UI, provider/connector breadth, ecosystem size, or overall product comparison |
| Tolaria `v2026-08-11` / `cb45f26649a7500e0bdb5dd0b8f0412e9c1daf4d` | Read Markdown/Wikilinks; perform controlled edits; detect conflict and reconcile; preserve source-successor identity and reject a wrong merge | `canonical_integrity`, `migration_recovery`, `source_citation_locator`, `secret_host_isolation`; real-file/editor receipts | Tolaria Desktop GUI, visual design, and its runtime |
| Obsidian official public format/API help, accessed 2026-08-11; API snapshot `obsidian@1.13.2` / `cc1744324150c632416857c98964f87b1574a5fc` | Preserve Markdown, Wikilinks, aliases, backlinks/outlinks, rename/move identity, and edit/reconcile behavior on real files | `canonical_integrity`, `migration_recovery`, `source_citation_locator`, `machine_reference_isolation`; real-file/editor receipts | Obsidian UI, Sync, marketplace, and complete Canvas UX |
| LLM Wiki behavior category (not a comparator project) | Agent-generated or updated knowledge retains provenance, revision, Authority, scope, sensitivity, and Ledger state; Source Revision is never rewritten | `canonical_integrity`, `secret_host_isolation`, `source_citation_locator`, `machine_reference_isolation` | Any named-product or superiority claim without a separately frozen comparator |
| Codex `0.148.0-alpha.15` / `gpt-5.6-luna` / `reasoning=max` | Three real task families: cold/new; resume/fork/concurrent-worktree; compaction/forget, including stale checkpoint and wrong task line | `codex`, `bounded_context`, `secret_host_isolation`, `selective_forget`; First Correct Action, Decision Preservation, Wrong-State Admission, and actual Provider bytes/tokens | Codex runtime ownership, UI, marketplace, or static/no-model smoke |
| OpenCode `1.18.16` / `deepseek/deepseek-v4-flash` | The same three real Host task families and wrong-state challenges with independently isolated Secret handling | `opencode`, `bounded_context`, `secret_host_isolation`, `selective_forget`; the same outcomes and actual Provider bytes/tokens | OpenCode runtime, UI, ecosystem, or config-only smoke |

Before every mapped task and Core gate passes, the only permitted statement is:

> Minimum Kernel compatibility is a release acceptance requirement and has not yet been qualified.

After every mapped task and Core gate passes on the exact artifact, the permitted technical claim
is bounded to:

> DeepLaw meets the frozen v0.13 Kernel compatibility baseline defined by the qualification protocol.

Neither statement permits a claim that DeepLaw equals or exceeds a whole comparator product, or
that it is perfect, SOTA, leading, fully verified, RC, or GA.

## Frozen real task set

### Host continuity

Codex requires at least three distinct runs using exact `codex-cli 0.148.0-alpha.15`, binary
SHA-256 `7645c3caf5607e4528eb3a15b12496c284c2a918939aed34e863c760c1b421e7`, requested model
`gpt-5.6-luna`, and `reasoning=max`. Authentication uses the supported official existing-login
seam without reading, copying, printing, or retaining auth. The receipt records the actual returned
model identity and date; the selector is not represented as an immutable model snapshot.

The Codex and OpenCode executable coordinates below were re-observed locally at
`2026-08-20T12:38:48Z`; this observation rotates the current input contract but is not Host task or
qualification evidence.

OpenCode requires at least three distinct runs using exact `1.18.16`, source commit
`a3647eb025c7615159d417dcc49fc39fdaeba65b`, selector
`deepseek/deepseek-v4-flash`, and expected response model `deepseek-v4-flash`. Its owner-only
`.env` is read only by the credential broker. Exact executable/package hashes are private inputs
and sanitized receipt fields; DeepLaw runners and scorers receive no Secret.

Across the six runs, the task set covers new thread, ordinary resume without task handle,
fork/child task, compaction, concurrent worktree, stale checkpoint, workspace divergence, wrong
task line, selective forget, and no-binding/ambiguous-binding Gap. The public journey is
`init/doctor -> task start -> task locate -> task-neutral host connect -> explicit session bind ->
new thread/resume -> fork -> compaction -> stale/wrong challenges -> selective forget`.

### Living Wiki

The retained real tasks cover alias/same-name identity, rename/move, external edit/reconcile,
backlink/outlink, source successor, wrong merge, protected/user-owned file protection,
full/incremental equivalence, Wiki-to-exact-Source drill-down, and the retained physical profile at
1k/10k/100k scale. Scale receipts inventory the actual artifact families and do not generalize one
file profile to every Statement, Relation, or Wiki layout.

### Evidence and Legal

Evidence tasks bind exact source bytes for PDF, DOCX, HTML, and Markdown; Document, Version,
Fragment, Locator, quote, effective date, exception/proviso/cross-reference, OCR critical token,
wrong version/false Authority, acceptable Gap, and exact Source drill-down. The current Gate also
requires the exact signed Legal Pack. Agent interpretation remains `legal_authority=false`; the
machine-only profile does not claim legal-expert review.

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

## Gate v8 and execution order

All 14 Core gates are required:

1. `canonical_integrity`
2. `migration_recovery`
3. `secret_host_isolation`
4. `bounded_context`
5. `legal_evidence`
6. `source_citation_locator`
7. `scale_performance`
8. `supported_platforms`
9. `reproducible_supply_chain`
10. `machine_reference_isolation`
11. `codex`
12. `opencode`
13. `selective_forget`
14. `timeline`

Gate v8 stays `assembly_enabled=false` with `awaiting_all_core_gate_pass` until source-specific
validators reopen every retained input and derive zero hard failures. Validator availability is a
code property, not evidence. Capability gates may remain `not_claimed` only when the capability is
not declared. Competitive gates remain independent.

Formal order is:

1. Candidate Full: exact 0.13.0 commit, reproducible wheel/sdist, exact-wheel journey, scale,
   required Python/3-OS matrix, SBOM/licenses/OpenVEX/provenance.
2. External Qualification: download the same artifact, run isolated Host/Evidence/Wiki/Context
   tasks, and retain only sanitized evidence.
3. Commercial Qualification: download the same artifact, reopen every source, and derive all 14
   Core gates, `assembly_enabled`, `release_ready`, and bounded machine technical claims.

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
