# DeepLaw v0.13 historical Gate v8 machine-evaluated qualification

Status: **historical compatibility protocol; not the active release gate**. This document preserves
the Gate v8 machine-only profile and v1/v2 contracts without rewriting their evidence semantics.
The active Kernel release boundary is Gate v9 with
[`benchmarks/v013/active-qualification-v3.json`](../benchmarks/v013/active-qualification-v3.json),
profile `kernel_release_core`. The tracked package remains `0.12.0`; no candidate artifact is bound
and no release decision is enabled.

The historical machine-readable contract is
[`contracts/v013-qualification-protocol.v2.schema.json`](../contracts/v013-qualification-protocol.v2.schema.json).
The historical frozen protocol is
[`benchmarks/v013/qualification-protocol-v2.json`](../benchmarks/v013/qualification-protocol-v2.json),
with its exact JSON-byte hash in
[`benchmarks/v013/qualification-protocol-v2.sha256`](../benchmarks/v013/qualification-protocol-v2.sha256).
The retained Gate v8 binding is
[`benchmarks/v013/active-qualification-v2.json`](../benchmarks/v013/active-qualification-v2.json).

## Profile boundary

`machine_evaluated_no_human_attestation` is an explicit evidence class. Agent output may be used
for development, challenge, deterministic reconciliation, scoring, and provenance checks, but it
cannot be labelled as Human Gold, human-authored, human-verified, legally authoritative, or
Owner-approved. The `human_review` section is optional and non-gating; its current status is
`not_executed` and authenticity is `not_claimed`.

This profile does not remove the need for a current Owner decision before an immutable tag or
public release. The active record therefore fixes `release_ready=false`,
`claim_eligible=false`, and `machine_qualification_claim_eligible=false`.

## Isolated execution domains

The formal run uses a small set of real security domains rather than treating every logical role
as an isolation boundary: three mutually invisible machine-reviewer sessions, a sealed
reference/freezer, the exact candidate and Host runner, owner-controlled Codex and OpenCode
credential brokers, scorer A, and scorer B plus deterministic arbitration/provenance. Reviewers
are always labelled machine reviewers. When they use the same model, the evidence says
`same-model-process-separated`; it does not imply independent human review.

Reviewers cannot see one another, a candidate, a runner, or a scorer. The exact candidate runner
receives only the exact artifact and selected source corpus; it cannot see reference labels,
scorer source, or a dotenv path. Scorers receive retained candidate output and evaluator-only
references, but cannot see candidate source, runner processes, or the other scorer. Process
receipts bind executable and process-tree hashes, PID lineage, input/output hashes, environment
key allowlists, read-only mounts, timestamps, and exit codes. Those receipts must be produced or
corroborated by the isolation launcher; a process-authored JSON statement alone is not proof of
isolation.

The reconciler may produce a machine proposal, not a Human Gold manifest. Reviewer disagreement
is a failure; it is not resolved by model majority or a fabricated approval. The arbitrator is a
deterministic exact replay of both scorer outputs. Any scorer disagreement fails the evaluation.

## Corpus and blind handling

Repository fixtures are development material only. The qualification holdout is repository
external, evaluator-labelled, and downgraded to development if used for tuning or diagnosis. The
final blind is released only after an exact candidate freeze. A final-blind failure or contamination
requires a new unseen blind corpus after repair; rerunning the same blind does not restore validity.

No compiler or Host process receives reference labels, expected identities, scorer state, or
private material. Missing, duplicate, malformed, symlinked, unreferenced, or path-escaping inputs
fail closed.

## Hash and scoring chain

All retained evidence uses strict UTF-8 JSON, duplicate-key rejection, non-finite-number
rejection, regular non-symlink files, path closure, orphan rejection, and SHA-256 over canonical
JSON with `record_sha256` excluded from the record body. The binding chain covers:

- protocol and agent-review inputs/outputs;
- machine semantic proposal;
- qualification and final-blind corpus bytes;
- exact candidate commit/tree/lock/wheel/sdist;
- runner, scorer A, scorer B, and arbitration identities;
- isolation receipt, typed evidence, and the final bundle manifest.

The External Qualification v4 validator is deliberately a structural preflight: it closes the
inventory, identities, hashes, corpus bytes, reviewer process receipts, reviewer output bytes, and
machine-reference bindings, but returns `qualification_passed=false`. It does not turn a
well-formed bundle into a Gate result. Commercial Qualification v8 reopens that bundle, runs every
typed parser against raw receipts, and is the first layer allowed to derive Core Gate results. The
release provenance v8 verifier then repeats the typed derivation and checks all three workflow run
identities before accepting `release_ready=true` for an exact candidate.

The Candidate Full inventory embedded in External evidence must be byte-identical to the
independently downloaded Candidate Full inventory. Candidate Full's public journey explicitly uses
the historical candidate-only v1 receipt and cannot claim or invent a future evidence run or
holdout. External Qualification exclusively uses the current v2 receipt, which binds the candidate
commit/tree/lock/wheel, Candidate Full run, External evidence run, Candidate Full raw-inventory
hash, and the exact runner source. This proves that the public wheel journey ran against the exact
candidate; it does not claim that the qualification holdout was executed. The workflow hashes that
receipt before handing it to the
repository-external runner and requires the retained bundle bytes to be identical afterward.
Commercial and Release also compare the active candidate's retained-manifest hash and the typed
SBOM/OpenVEX/license/provenance digests with the exact files selected for publication.

Scorers recompute case identity, expected/observed values, duties, hard failures, and false
authority from retained candidate raw output. Candidate execution binds the raw-output hash, and
replacing that output invalidates existing scorer evidence. Caller-authored `passed`, `count`,
`facts`, or aggregate fields are not accepted. Missing or duplicate cases fail. Hard failures and
false-authority observations have a maximum of zero. Commercial scale additionally requires one
actual-candidate process receipt for each 1k/10k/100k row; periodic synthetic diagnostics remain
development evidence and are claim-ineligible.

The post-public receipt is versioned separately from the historical v1 contract. The current
release workflow emits and validates
[`contracts/post-public-verification.v2.schema.json`](../contracts/post-public-verification.v2.schema.json).
Its `release_binding` records the immutable tag, exact commit and tree, and the SHA-256 values of
the downloaded `commercial-release-manifest.json` and `SHA256SUMS`. This receipt is produced only
after anonymous public redownload, signature verification, and attestation verification; it never
becomes a pre-public Core Gate or a prerequisite of `release_ready`. The v1 schema remains a
historical compatibility contract and is not rewritten.

When a draft or public prerelease is resumed, the workflow constructs an allowlist from the local
publish directory and the already-expected `post-public-verification.json` receipt. Any remote
asset outside that allowlist fails closed before upload or state changes; no unexpected remote
asset is deleted.

## Host pins

The current v3 qualification run does not freeze a Desktop-specific Codex build in a
source-controlled manifest. Instead, the owner supplies an external, owner-only regular file
`deeplaw.host-exact-identity/v1` for each run. Its bounded version and 64-hex SHA fields are
validated for shape; the fixed model, effort, selector, runtime, and authentication-prohibition
fields remain closed. The exact input bytes are retained as
`candidate-inventory/host-exact-identity.json` and their source SHA is bound to every process
receipt and the candidate bundle.

Codex must be the exact regular, non-symlink, single-link executable described by that input. An
OpenCode selector symlink may preserve its source fact, but execution is bound only to its resolved
regular, single-link target. The owner-controlled credential brokers use the locally authenticated
Hosts without giving `HOME`, `CODEX_HOME`, auth files, or credential values to the DeepLaw runner,
scorer, or evidence assembler. The returned response model identifier is recorded separately from
the request identity.

The request-model values identify requests, not immutable model weights. Formal evidence records
the observed binary/package hashes, execution date, returned model identifier, per-Host identity
SHA, and exact external-input SHA. These inputs identify future evidence; they are not Host
qualification results. Until machine evidence is actually executed and revalidated, all gates
remain `not_executed`.

## Disposition

The v2 active record intentionally retains the current `uv.lock` hash while leaving source,
artifact, corpus, runner, scorer, arbitration, and isolation hashes null. It is a pending machine
evaluation specification, not a result report. No placeholder evidence may be generated to fill
those nulls, and no machine result can authorize a tag or public release.

The historical `candidate-gold-binding-receipt` filename is retained only as a versioned path for
compatibility. Its current v2 schema and status are machine-reference bindings and cannot carry a
Human Gold or human-attestation claim.
