# DeepLaw v0.13 Pass 11 final disposition

Status: **source candidate; release gates incomplete** (2026-08-11).

```text
package_version=0.12.0
release_gate_passed=false
claim_eligible=false
competitive_claim_eligible=false
release_ready=false
version_change_authorized=false
tag_authorized=false
publication_authorized=false
```

This is the current Pass 11 decision. It supersedes no historical report and does not rewrite the
Pass 8/Pass 10 facts. It separates useful local development evidence from failed qualification
attempts and unexecuted external gates.

## Exact artifact candidate

The fresh reproducible distribution candidate is bound to clean commit
`9aea8598b231ae85a70a478f37682a9b7a17f024`, tree
`425d07324c22de051e2bf00fe04e7a9b80de9e2b`, package `0.12.0`.

| Artifact | Bytes | SHA-256 |
| --- | ---: | --- |
| `deeplaw-0.12.0-py3-none-any.whl` | 1,243,859 | `1e997e94b619e228cd9a692b51d20c7ff3ca001fd5db74ce9d0a005397ca0976` |
| `deeplaw-0.12.0.tar.gz` | 14,276,324 | `5c19c9fa598aefb328cae9480b3030b70cbc6947c3a23cb475741773c2a95806` |

Two independent local builds produced byte-identical wheel and sdist inventories. The retained
reproducible-build report is record-digest verified. On Darwin, isolated Python `3.11.15`,
`3.12.13`, and `3.13.13` environments each installed the exact wheel, observed both CLI and package
version `0.12.0`, passed dependency checks, uninstalled the package, and confirmed it was no longer
importable. The formal distribution lifecycle additionally passed wheel/sdist install-uninstall,
locked constraints, and the `0.6.0 → 0.12.0` upgrade on Python `3.11.15`.

A CycloneDX `1.5` SBOM with 128 components and a 66-package license inventory were generated. The
license inventory has no blocked or review-required entries. The default, build, discovery, and
document-engine dependency profiles passed the current audit; the document-engine result records
four exact OpenVEX-covered advisories for code outside the closed execution path.

These are local artifact-development results, not a complete release chain. The wheel/sdist were
not signed, uploaded, or publicly redownloaded. No provenance attestation was created. Linux,
Windows, the nine-cell full test matrix, exact-wheel Vault snapshot/restore/rollback, and support-
bundle redaction were not executed. The machine-readable local evidence is under
[`../benchmarks/release/evidence/pass11-final-artifact-2026-08-11/`](../benchmarks/release/evidence/pass11-final-artifact-2026-08-11/).

## Executed

- Pass 10 invalidation reproduced at the frozen baseline: Statement Gold byte mismatch, Codex
  receipt argv drift, and prompt label/marker/exact-ID contamination.
- Candidate prompts, Vault/config, evaluator Gold, and scorer paths were separated and covered by
  fail-closed contamination/admission tests before any real-model run.
- Basic CLI help, direct reconcile, sink help correction, read-only `host connect`, and the frozen
  caller/contract inventory were implemented without deleting compatibility surfaces.
- Three authorized Codex App Server token-attribution workflows and one isolated
  OpenCode/DeepSeek workflow executed as claim-ineligible candidate observations.
- One exact-candidate synthetic Obsidian Desktop load/verify/rename/edit/reconcile/stale-conflict
  recovery seam executed with inspected captures and no retained user content or local path.
- Wiki/Statement 1k/10k/100k construction diagnostics, focused professional-Evidence regressions,
  current Tolaria source pin, reproducible distributions, local dependency/SBOM/license checks,
  and Darwin three-Python install smoke executed.
- The 69db28c query/graph scale report was preserved without rebinding after the visible
  development Gold rotated. Its current-checkout verification now correctly reports a Gold byte
  mismatch; the runner had recorded that Gold was not read or scored, so these remain historical
  candidate measurements rather than qualification evidence.

## Failed observations

- None of the three Codex A/B/C/D workflows passed all conditions. The full 19-operation schema
  condition failed in every attempt; exact MCP failed in every attempt; only one condition was
  evaluator-scoreable and its First Correct Action was `0.0`. No operation profile was admitted.
- The isolated OpenCode `1.18.16` / `deepseek-v4-flash` run exited normally but did not call
  `knowledge_support`; it produced no Provider Capsule or neutral structured output and was
  independently `not_scored`.
- The retained unsigned synthetic legal development report has Document Recall `0.0`, Exact
  Segment Recall `0.0`, and Exception Recall `0.0`. Temporal and Authority admission were not
  weakened to hide the failure.

## Not executed or externally blocked

- uncontaminated multi-state continuity across new/resume/fork/compaction/concurrent worktree,
  stale/wrong route, forget, and conflict recovery with independent Human Gold;
- physically and permission-isolated qualification holdout and final blind;
- same-condition Host-only, Host-native-memory, and Host-plus-DeepLaw outcome comparison;
- Tolaria Desktop open/edit/external-change/reconcile;
- independent Human/Agent paired Wiki task and licensed professional-document task;
- 10k/100k incremental equivalence, persistent MCP, cache invalidation, isolated RSS stability,
  Relation scale, and 10k/100k snapshot/restore;
- licensed/signed legal corpus, independent legal reviewer, and critical-token review;
- Linux and Windows exact-candidate execution and the complete platform/Python matrix;
- signed provenance, artifact signature, public upload/redownload, and support-bundle qualification.

## Release decision

The local reproducible artifact and install results cannot override the failed Host observations or
missing Core gates. The package stays `0.12.0`, the classifier stays `source_candidate`, and the pull
request must remain Draft. No version change, tag, publication, or readiness promotion is allowed
without every gate and explicit Owner approval.
