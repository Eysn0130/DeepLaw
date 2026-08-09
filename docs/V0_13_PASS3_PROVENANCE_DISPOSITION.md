# DeepLaw v0.13 Pass 3 provenance disposition

Status: **source candidate; provenance path remains fail closed; not released** (2026-08-09).

This report records the Pass 3 evidence-contract correction at the reviewed source baseline. It
does not create a release, qualify a Host, prove Human Gold, or convert repository development
fixtures into external evidence.

## Final disposition

```text
baseline_commit=134c3fedce38e2fe81c52a4867d864fb1d263df7
baseline_tree=6b1f81ca54dc47a105a5eb8919ca0e3f51520ef6
package_version=0.12.0
release_gate_passed=false
commercial_release_eligible=false
quality_protocol_eligible=false
claim_eligible=false
competitive_claim_eligible=false
final_disposition=source_candidate_remains_not_released
```

The baseline is the documentation-rotated source candidate. The tracked report cannot bind its
own future commit or file bytes. No v0.13 version, tag, RC, GA, signature, registry upload,
public artifact, or publication was created.

## 1. What Pass 3 establishes

Pass 3 separates three things that must not be conflated:

1. **Self-reported v1 observations** are a rejected legacy input. A closed JSON object and a
   self-consistent hash do not prove that a command ran, that the claimed model or Host existed,
   that runs were independent, that a scorer saw the right bytes, or that a hard-zero counter was
   computed from raw evidence.
2. **Provenance-bound v2 contracts** define the input and receipt shape needed for a future
   validator. They are additive contracts, not a claim that the validators or external evidence
   already exist.
3. **Release assembly** remains disabled until every required Core Gate has a dedicated raw-input
   validator. Structural JSON Schema validation is necessary, but it is not a gate result and is
   not a release decision.

The local `benchmarks.release.semantic_evidence.validate_report` seam remains a compatibility
verifier for the legacy `commercial-evidence-report/v1` shape. It validates bounds, closed fields,
digests and bindings, then deliberately adds `legacy_self_report_not_provenance_bound` to every
declared Core result and returns `release_ready=false`, `claim_eligible=false`, and
`competitive_claim_eligible=false`. It must not be described as a provenance scorer.

## 2. P0 precondition failures A/B/C/D

The following failures are the reason the v2 assembly policy is disabled. Each is a provenance or
decision-boundary failure, not a product-quality score.

| Failure | Reproduction / root cause | Risk | Current disposition |
| --- | --- | --- | --- |
| **A — fake Host/model/runner identity** | A v1 artifact can be internally consistent while its Codex command or model is replaced with a made-up executable/model. The observation records caller-authored environment and argv; no raw process receipt binds them to an executable run. | Model substitution, wrong tool or parameter, and false Host acceptance can be presented as a successful Core result. | The semantic verifier rejects the legacy self-report. The v2 Codex contract requires exact Host/model/runner constraints and independent raw receipts; its dedicated validator is still missing. |
| **B — hash-only or arbitrary-byte evidence** | Hash-correct inventory records and enclosing report hashes prove only byte equality to the supplied files. They do not establish that protocol, Gold, wheel/sdist, or source bytes are the required frozen and independently produced artifacts. A shape-only fixture can therefore look complete without semantic provenance. | Arbitrary bytes, repository fixtures, or caller-selected “external” files could be relabelled as Human Gold, protocol, or release evidence. | v2 binds actual result files, input records, candidate/protocol/threshold/Gold/classification hashes and corpus role/source. The raw validator and independent external corpus are absent; no release assembly. |
| **C — fabricated run count and counters** | One observation can self-report `run_count=3`, passing thresholds, zero hard failures, and clean redaction without distinct `run_id`, Host reports, scorer rows, or raw input references. | Repeated-run requirements, hard-zero safety checks, metrics, and redaction can all be fabricated in one JSON record. | v2 Gate Result requires input-bound execution rows, distinct `run_ids`, unique dimensions, metric/hard-failure/redaction `input_refs`, and result provenance. All Core raw validators remain missing, so the gate is not executed. |
| **D — caller decision / legacy assembler bypass** | A caller can try to supply `passed`, `release_ready`, or eligibility booleans, or a workflow can keep invoking the historical v5/no-model assembler for a v0.13 package. A decision field is not evidence. | A v0.13 release could be downgraded to v5 semantics or a forged pass receipt could bypass independent Core decisions. | The v0.13 assembler rejects caller-supplied decisions and legacy v1 observations. `commercial-gates.yml` intentionally still invokes the historical assembler; that assembler safely rejects v0.13 because policy requires manifest v6. The provenance assembler is disabled with `blocked_missing_validator`. |

These failures are fixed as **fail-closed controls**, not “passed” gates. A repository test that
asserts rejection is evidence of the boundary, not evidence of a successful commercial run.

## 3. Provenance-bound v2 contracts and data flow

The additive contract set is:

- `contracts/provenance-bound-gate-result.v1.schema.json` — one closed result per gate. It binds
  the classification, candidate commit/tree and artifacts, frozen protocol/threshold/Gold/corpus,
  validator identity/version, status, input-bound command/environment execution rows, independent
  run IDs and dimensions, metrics, hard failures, failures, redaction, raw-input references, and a
  result digest.
- `contracts/commercial-evidence-report.v2.schema.json` — a closed collection of references to
  Gate Result files. It contains no embedded observations, caller-supplied statuses, or release
  decisions.
- `contracts/v013-release-gate-classification.v2.schema.json` plus
  `benchmarks/release/v013-gate-classification-v2.json` — the closed mapping of each gate to raw
  input schema versions, validator identity/version, thresholds, hard-zero derivation, run
  dimensions, corpus roles, and execution platforms. The development classification is explicitly
  `assembly_policy.assembly_enabled=false` with reason `blocked_missing_validator`.

Reviewed contract bytes for this disposition:

| File | SHA-256 |
| --- | --- |
| `contracts/provenance-bound-gate-result.v1.schema.json` | `20acbd001e0044397979241236c8d27c4509246986d7a2a0b6f9ed97dd484ac6` |
| `contracts/commercial-evidence-report.v2.schema.json` | `0966f1c412fe220193d9dcad9b6be791b29ebb5d7ba730a1e774abda494c79ed` |
| `contracts/v013-release-gate-classification.v2.schema.json` | `050ab23c714e65e8ffd0121de975c012e1ea4ff148f294c47f77f900c0c67ef9` |
| `benchmarks/release/v013-gate-classification-v2.json` | `4efbb8096f0fc57fbb8cc1ffe76e794e3bc6022b0969d1d980dfc80c112a90e2` |

These are repository-visible development contracts. Their hashes do not make them external
qualification evidence.

The intended future flow is:

```text
raw run / Host / evaluator / source records
  -> dedicated per-gate validator
  -> provenance-bound Gate Result v1 (one file per gate)
  -> commercial-evidence-report v2 (references only)
  -> v2 classification + semantic binding checks
  -> v0.13 manifest-v6 assembler
  -> release policy and publish/public-redownload revalidation
```

No step may infer a raw run, scorer row, external corpus, or hard-zero result from a hash, a
model-generated summary, a caller boolean, or a repository fixture. Until all required Core
results are present and independently validated, every Core status is `not_executed` for this
disposition.

## 4. Core Gate raw contracts and validator disposition

The classification declares the following 12 required Core Gates. The `validator_id@version`
values below are **declared validator identities**, not implementations. The only current generic
verifier is the legacy v1 structural/semantic compatibility seam described in section 1; it is
intentionally incapable of passing a Core Gate. `Draft202012Validator` checks contract shape only.
Parenthetical local checks are development regressions or runtime integrity checks, not
provenance-bound commercial scorers.

| Core Gate | Accepted raw schema version(s) | Declared validator | Existing local scorer/verifier seam (not a release scorer) | Implementation status | Pass 3 disposition |
| --- | --- | --- | --- | --- | --- |
| `canonical_integrity` | `deeplaw.v013-runtime-stability-report/v1` | `deeplaw.v013.gate.canonical_integrity@0.1.0` | `AutonomousKnowledgeStore.verify` and integrity regression tests; no raw Gate Result validator | `blocked_missing_validator` | `not_executed` |
| `migration_recovery` | `deeplaw.release-capability-migration/v1`; `deeplaw.release-capability-rollback/v1` | `deeplaw.v013.gate.migration_recovery@0.1.0` | Local migration/reconciliation tests; no raw migration/rollback scorer | `blocked_missing_validator` | `not_executed` |
| `secret_host_isolation` | `deeplaw.real-semantic-host-report/v2` | `deeplaw.v013.gate.secret_host_isolation@0.1.0` | Deterministic canary/environment-isolation regressions; no real-Host raw validator | `blocked_missing_validator` | `not_executed` |
| `bounded_context` | `deeplaw.provider-knowledge-capsule/v2`; `deeplaw.agent-context-envelope/v1`; `deeplaw.query-audit-read/v1` | `deeplaw.v013.gate.bounded_context@0.1.0` | Capsule/Context bounds, redaction and query-trace tests; no raw payload Gate Result scorer | `blocked_missing_validator` | `not_executed` |
| `legal_evidence` | `deeplaw.authoritative-evidence-quality/v1` | `deeplaw.v013.gate.legal_evidence@0.1.0` | Local Legal Pack/evidence boundary tests only; no signed Pack or independent legal evaluator | `blocked_missing_validator` | `not_executed` |
| `source_citation_locator` | `deeplaw.statement-evidence-map/v1`; `deeplaw.statement-evidence-receipt/v1`; `deeplaw.citation-audit/v1` | `deeplaw.v013.gate.source_citation_locator@0.1.0` | Local Statement/evidence/locator regressions; no raw citation scorer | `blocked_missing_validator` | `not_executed` |
| `scale_performance` | `deeplaw.v013-scale-performance-report/v1`; `deeplaw.v013-query-graph-scale-report/v1`; `deeplaw.v013-runtime-stability-report/v1` | `deeplaw.v013.gate.scale_performance@0.1.0` | Development scale runners/reports and bounded diagnostics; no current-candidate raw qualification validator | `blocked_missing_validator` | `not_executed` |
| `supported_platforms` | `deeplaw.platform-release-gate/v1` | `deeplaw.v013.gate.supported_platforms@0.1.0` | No current 9-row matrix verifier; local platform checks are not the matrix gate | `blocked_missing_validator` | `not_executed` |
| `reproducible_supply_chain` | `deeplaw.reproducible-build-report/v2` | `deeplaw.v013.gate.reproducible_supply_chain@0.1.0` | Historical/local reproducible-build checks; no current provenance/SBOM/public-redownload scorer | `blocked_missing_validator` | `not_executed` |
| `human_gold_isolation` | `deeplaw.semantic-gold/v1`; `deeplaw.semantic-gold-freeze/v1`; `deeplaw.v013-qualification-protocol/v1` | `deeplaw.v013.gate.human_gold_isolation@0.1.0` | Protocol/schema and local isolation assertions; no independent evaluator receipt or external Gold validator | `blocked_missing_validator` | `not_executed` |
| `codex` | `deeplaw.real-semantic-host-report/v2`; `deeplaw.real-host-compile-command/v1`; `deeplaw.autonomy-evaluation-report/v1` | `deeplaw.v013.gate.codex@0.1.0` | Plugin/static smoke and Context regressions only; no real Codex raw-run validator | `blocked_missing_validator` | `not_executed` |
| `selective_forget` | `deeplaw.autonomy-evaluation-report/v1` | `deeplaw.v013.gate.selective_forget@0.1.0` | Local forget/withdrawal regressions; no raw selective-forget contract/scorer | `blocked_missing_raw_contract` | `not_executed` |

The eleven `blocked_missing_validator` statuses and the one
`blocked_missing_raw_contract` status are classification facts. They are not failures converted
to passes, and they do not authorize assembly. Every Core Gate remains `not_executed` in this
report, including gates with useful local development tests.

### Codex exact contract

The Codex Core Gate is frozen to:

```text
minimum_distinct_run_count=3
required_unique_dimensions=run_id,host,model,platform,task_case
host=codex
tool_version=0.145.0
model_id=gpt-5.6-luna
argv_prefix=[codex, exec, --ephemeral]
```

No real Codex run, repository-external frozen corpus, independent scorer, or Host receipt was
executed for this candidate. Static/plugin smoke evidence cannot satisfy the exact contract.

## 5. Commercial workflow safety

The release workflow remains deliberately conservative while the provenance work is incomplete:

- `.github/workflows/commercial-gates.yml` still invokes `benchmarks.release.commercial_release`
  (the historical assembler) in its existing audit/assembly block. It has not been silently
  switched to the disabled provenance assembler.
- The historical assembler is closed for the v0.13 line. Release policy selects manifest v5 for
  package `0.12.x` compatibility and requires manifest v6 for `0.13.x`; a v5/no-model result for
  `0.13` is rejected.
- `benchmarks.release.v013_commercial_release.assemble_manifest` rejects legacy v1
  self-reported observations, caller-supplied derived decisions, invalid v2/classification
  versions, and `assembly_enabled=false`. It cannot manufacture a v6 manifest from a shape-only
  fixture.
- Publish and post-release semantic validation are downstream checks; they do not make missing
  raw validators or external evidence appear.

This preserves the v0.12 package line and prevents a v0.13 downgrade or forged pass receipt.

## 6. Continuity read-only recheck

The ordinary Context route-override boundary was rechecked read-only. Public Python Context and
MCP Context accept canonical `task`, optional `goal`, `query_target`, and `task_binding`; they do
not expose an independent route-text override. The internal route seam derives the route digest
from canonical task text, while `goal` remains part of retrieval query planning. A public caller
cannot submit query text A with route text B through the current API/schema.

The checkpoint lifecycle read-only recheck also found no new runtime defect: Sink record/run and
working-checkpoint writes, current-head CAS, stale-head `checkpoint_head_conflict`, Context
requery, forget/withdrawal, and owner/domain route-projection rebuild remain bounded and
fail-closed. This is development continuity evidence, not a Core Gate result.

**Continuity disposition:** `public route override=not_reproduced`; **runtime unchanged**.

The focused route and checkpoint reproduction tests remain useful regression evidence, but their
passing status does not qualify a real Host, Human Gold, legal pack, scale, or release artifact.

## 7. Nine historical skips and their disposition

The nine retained skips are explicit non-results. A skip is never counted as a pass.

| Historical lane | Class | Disposition |
| --- | --- | --- |
| Statement scale 10k | Core / scale | `required not_executed` |
| Statement scale 100k | Core / scale | `required not_executed` |
| Relation truncation 500/5000 | Core / retrieval bound | `required not_executed` |
| Wiki wrong merge | Core / Wiki integrity | `required not_executed` |
| Wiki alias collision | Core / Wiki identity | `required not_executed` |
| Wiki cycle | Core / Wiki graph | `required not_executed` |
| Historical v0.6 wheel | Historical compatibility | `separate compatibility not_executed` |
| Windows native ACL | Platform | `macOS not_applicable`; Windows evidence remains required |
| Windows native junction | Platform | `macOS not_applicable`; Windows evidence remains required |

The two Windows rows are not successes on macOS. The historical wheel row is not a current v0.13
Core pass. The six Core rows remain release-blocking until their required evidence is executed and
validated by the appropriate raw-input validator.

## 8. External and release gates not executed

The following remain `not_executed` and cannot be inferred from local source or development data:

- repository-external qualification/final-blind source and Human Gold, including compiler/evaluator
  isolation receipts and fresh unseen holdouts;
- real Host runs, especially the exact three-run Codex contract; Claude/OpenCode/DeepSeek are not
  silently substituted, and no Provider call was made;
- the exact signed/verified Legal Pack, independent legal scoring, primary-evidence version and
  locator checks, and legal Authority hard-zero checks;
- current-candidate 10k/100k scale, Relation truncation, 10,000-request RSS, eight-reader/cache
  lanes, and any scale result after this candidate freeze;
- the full 9-row Linux/macOS/Windows × Python 3.11/3.12/3.13 platform matrix;
- fresh current-candidate wheel/sdist SBOM, license/OpenVEX/provenance checks, signatures, public
  upload, and public redownload verification;
- Timeline, semantic restore, fork/merge lifecycle, and other deferred capabilities; they remain
  `not_claimed_only`, not Core passes.

No private credential, Provider session, current Host login, or local secret was used or recorded.
No private absolute path is part of this report.

## 9. Local verification boundary

The requested focused checks are contract/regression checks only:

```bash
uv run --frozen pytest --strict-markers -q \
  tests/test_v013_commercial_evidence_semantics.py \
  tests/test_v013_commercial_release_gate.py \
  tests/test_v013_provenance_contracts.py

git diff --check -- docs/V0_13_PASS3_PROVENANCE_DISPOSITION.md
```

Passing these checks verifies the fail-closed contract and documentation diff. It does not change
the statuses above, does not execute external inputs or Hosts, and does not authorize release.

## Final decision

The v2 contracts correctly require provenance-bound Gate Results, but the required raw validators
and external evidence are not present. The historical self-report path is rejected; the commercial
workflow remains safe for v0.13; the continuity route-override audit found no new defect and made
no runtime change. Therefore:

```text
source_candidate_remains_not_released
```
