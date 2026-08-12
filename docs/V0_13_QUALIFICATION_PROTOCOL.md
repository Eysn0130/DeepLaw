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
