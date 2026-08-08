# DeepLaw v0.13 Source-Candidate Qualification Protocol

Status: **protocol-frozen candidate binding pending** (2026-08-08). The package remains
`0.12.0`; this document does not authorize a release, RC, GA, or a competitive claim.

The machine-readable contract is
`contracts/v013-qualification-protocol.v1.schema.json`. The frozen candidate protocol is
`benchmarks/v013/qualification-protocol-v1.json`; its exact bytes are bound by
`benchmarks/v013/qualification-protocol-v1.sha256`. The sidecar hash is calculated over the
JSON bytes as stored, including its final newline.

## Why this is not a result

The protocol fixes the measurement design and the failure rules before any final result is read.
It deliberately has no candidate wheel, commit, external holdout, Gold, scorer, host receipt, or
result hash bound yet. Every metric and external gate therefore has status `not_executed`; this is
not a score of zero and cannot be converted into a pass by an empty denominator.

The repository-visible v0.13 fixtures are development material. A repository fixture, including a
fixture whose filename says `holdout`, is not a qualification holdout or a final blind holdout under
this protocol. A holdout used for diagnosis, tuning, or repair is automatically downgraded to the
development layer.

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

Real Codex (three isolated runs), OpenCode/DeepSeek (three isolated runs), Human Gold scoring, the
exact signed 28-source legal pack, 10k/100k statement/relation/Wiki scale, 10,000-request RSS,
eight-reader concurrency, the three-OS/Python matrix, and fresh wheel/SBOM/provenance/redownload
checks are all `not_executed` until their independently reproducible prerequisites are attached.
No ambient credential or current desktop session is an admissible substitute.

Until every required gate has real evidence, `quality_protocol_eligible` and
`competitive_claim_eligible` remain false and the release disposition remains
`not_released_source_candidate`.
