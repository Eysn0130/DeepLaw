# DeepLaw v0.10.0 release notes

DeepLaw `v0.10.0` completes the engineering portion of the **1.0 Quality and Superiority Closure**
milestone. DeepLaw 2.0 remains the product brand; it is not the package version.

Machine decisions:

```text
commercial_release_eligible=true
quality_protocol_eligible=true
competitive_claim_eligible=false
```

## Delivered

- DeepLaw Evaluation Protocol v1 with public Benchmark, fixed component weights and thresholds,
  non-averagable hard failures, and an explicitly non-secret time-frozen holdout.
- Actual autonomy/security cases over the shipped commit coordinator, grant policy, lifecycle,
  retrieval, disclosure controls, and Ledger hash chain.
- Actual source-bound bilingual `deterministic-v2` Typed Compiler quality scoring.
- One offline runner that generates the complete summary, four component reports, a readable report,
  functional scoring digest, and checksum inventory; a second mode independently verifies every
  emitted byte.
- Release eligibility bound to a clean strict post-freeze commit and the exact candidate wheel
  SHA-256. Source-tree reports remain development-only.
- Evaluation artifacts included in the release manifest, root checksums, Sigstore OIDC inputs, and
  GitHub provenance attestations.
- Commercial release manifest v3 with an explicit `quality_protocol_eligible` decision and corrected
  comparative evidence gaps.
- Restored DeepLaw 2.0 product imagery and synchronized bilingual branding, version identity,
  architecture explanation, examples, keywords, and evidence boundaries.

## Corrected route

The prior requirement for evaluator-secret data and signatures from two independent institutions
has been retired as the DeepLaw core quality and release gate. A signature authenticates a key and
bytes; it does not by itself prove correctness, independence, or Authority. Independent
replication remains optional.

Historical external-evaluation schemas and runners are retained for reproducibility and optional
comparative use. The v0.7 proposal/review implementation is likewise retained for source-derived
compilation, untrusted imports, migration, rollback, and compatibility. Neither route is the
default for admitted Agent-derived knowledge.

## Claim boundary

The exact release can state that it passed DeepLaw Evaluation Protocol v1. It cannot state that:

- the public holdout was secret, unseen, or contamination-free;
- deterministic typed extraction proves model cross-document synthesis;
- no future task can fail;
- DeepLaw is better than a named system that was not executed.

Real Codex, Claude Code, and OpenCode model tasks, actual same-condition named-baseline runs, paired
confidence intervals, and complete comparative cost/failure records remain absent. Therefore
`competitive_claim_eligible=false`.

## Upgrade

No autonomous Ledger schema change is required from v0.9.0. Before upgrading, create and verify an
explicit snapshot. Install the exact `v0.10.0` wheel, run `deeplaw knowledge doctor` and
`deeplaw knowledge autonomy verify`, then rebuild only the derived layer. See
[`INSTALL_UPGRADE_ROLLBACK.md`](INSTALL_UPGRADE_ROLLBACK.md).

Rollback restores the pre-upgrade snapshot before installing v0.9.0. Do not point an older binary
at a Vault whose canonical state has changed after the snapshot.
