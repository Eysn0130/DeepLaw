# DeepLaw v0.13 machine-evaluated qualification

Status: **protocol frozen; machine-evaluation inputs pending**. This document defines the new
machine-only profile and does not alter the historical v1 protocol or the existing v1 active
qualification record. The tracked package remains `0.12.0`; no candidate artifact is bound and
no release decision is enabled.

The machine-readable contract is
[`contracts/v013-qualification-protocol.v2.schema.json`](../contracts/v013-qualification-protocol.v2.schema.json).
The frozen protocol is
[`benchmarks/v013/qualification-protocol-v2.json`](../benchmarks/v013/qualification-protocol-v2.json),
with its exact JSON-byte hash in
[`benchmarks/v013/qualification-protocol-v2.sha256`](../benchmarks/v013/qualification-protocol-v2.sha256).
The pending active binding is
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

## Isolated roles

The frozen configuration contains thirteen logical roles, including at least three independent
semantic reviewers:

1. protocol freezer;
2. semantic reviewers A, B, and C;
3. adversarial reviewer;
4. deterministic reconciler;
5. corpus packager;
6. reference vault;
7. exact candidate runner;
8. independent scorers A and B;
9. deterministic arbitrator;
10. provenance auditor.

Every role is a separate process boundary with no Secret visibility. Reviewers cannot see one
another, a candidate, a runner, or a scorer. The exact candidate runner receives only the exact
artifact and selected source corpus; it cannot see reference labels. Scorers receive compiled
output and evaluator-only references, but cannot see candidate source, runner processes, or the
other scorer. Shared filesystems, IPC, and raw transcript sharing are forbidden; input mounts are
read-only.

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
commit/tree/lock/wheel, Candidate Full run, External evidence run, qualification-holdout hash, and
the exact runner source. The workflow hashes that receipt before handing it to the
repository-external runner and requires the retained bundle bytes to be identical afterward.
Commercial and Release also compare the active candidate's retained-manifest hash and the typed
SBOM/OpenVEX/license/provenance digests with the exact files selected for publication.

Scorers recompute case identity, expected/observed values, duties, hard failures, and false
authority from raw rows. Caller-authored `passed`, `count`, `facts`, or aggregate fields are not
accepted. Missing or duplicate cases fail. Hard failures and false-authority observations have a
maximum of zero.

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

Codex is pinned to `codex-cli 0.148.0-alpha.9`, binary SHA-256
`6170ff5578170ee9b74ad92bfcff96e6186f41d02b60815a7c2b01ad424c754f`, request model
`gpt-5.6-luna`, and `reasoning=max`. Authentication is checked only through `codex login status`;
authentication material is not read.

OpenCode is pinned to `1.18.16`, source commit
`a3647eb025c7615159d417dcc49fc39fdaeba65b`, executable SHA-256
`a41776bf64c75786d6baf531b840ffb873c090d7c44793ae2dd4b1896de56a1f`, package SHA-256
`d40af2479740f8ad3a32b700e9a907794ba4314c926d0e805c20fe39751d8722`, selector
`deepseek/deepseek-v4-flash`, and expected response model `deepseek-v4-flash`. It uses the Host
provided Bun runtime and an owner-only external dotenv parsed without shell evaluation.

These pins identify future evidence inputs; they are not Host qualification results. Until the
machine evidence is actually executed and revalidated, all gates remain `not_executed`.

## Disposition

The v2 active record intentionally retains the current `uv.lock` hash while leaving source,
artifact, corpus, runner, scorer, arbitration, and isolation hashes null. It is a pending machine
evaluation specification, not a result report. No placeholder evidence may be generated to fill
those nulls, and no machine result can authorize a tag or public release.

The historical `candidate-gold-binding-receipt` filename is retained only as a versioned path for
compatibility. Its current v2 schema and status are machine-reference bindings and cannot carry a
Human Gold or human-attestation claim.
