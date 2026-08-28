# DeepLaw v0.13.0 release notes

Status: **release candidate contract; not yet released**. The construction branch remains at
package `0.12.0` until its integration tree passes CI. These notes become release facts only when
the exact `0.13.0` wheel/sdist, Gate v9 receipts, tag, public GitHub Release, and anonymous
redownload verification are all bound by the final release manifest.

## Kernel release scope

DeepLaw v0.13 is one local-first governed Knowledge OS with three product roles on the same shared
kernel: Task Continuity, Source-native Evidence Library, and Living Wiki. It preserves immutable
Source Revisions, stable identities, CAS/Ledger provenance, Authority, scope, sensitivity,
bitemporal state, lifecycle, grants, receipts, contradictions, Gaps, backup, recovery, and explicit
forgetting. `knowledge_support` remains read-only; `knowledge_sink` is a separate owner-granted
mutation process; `law_support` remains an isolated trust boundary.

The active qualification contract is Gate v9 with 13 mandatory Kernel Release Core gates. A
release requires an exact Candidate Full run, Linux/macOS/Windows on Python 3.11/3.12/3.13, one
byte-reproducible wheel/sdist pair, a 10,000-active-object Vault qualification, three real Codex
tasks, three real OpenCode tasks, and closed supply-chain/provenance receipts. Missing or failed
Core evidence keeps `release_ready=false` and forbids the tag and Release.

## Supported boundary

- Up to 10,000 active governed Knowledge Objects per Vault is the v0.13 qualified commercial
  boundary, subject to the exact 10k receipt.
- More than 10,000 objects is experimental and unqualified for v0.13.
- 100,000-object sharding/bundling is deferred to v0.14 design work.
- Provider-visible Knowledge Capsules retain the existing 65,536-byte hard limit.
- Semantic v3 finalization keeps the Vault-wide existing-identity sample local and
  inventory-hash-bound; Provider context retains aggregate facts plus exact
  observation-relevant canonical identities and a deterministic per-duty reference
  sample within the same 65,536-byte limit.

## Separate non-claims

The following do not block the generic Kernel when all Core gates pass, but remain unavailable or
unclaimed unless their own evidence passes:

- official signed Legal Pack GA, legal-expert or Human attestation;
- semantic restore;
- Claude qualification;
- GUI/Desktop interoperability;
- machine-reference isolation, comparative blind holdouts, review panels, scorer A/B, arbitration,
  comparative incremental benefit, superiority, or SOTA.

The general Evidence Library may process owner-provided professional or legal material while
preserving exact Source/Version/Fragment/Locator evidence. Model interpretation is never legal
Authority, and an absent signed Legal Pack cannot be represented as official coverage.

## Evidence rule

Source presence, local tests, diagnostics, a successful CI run, or a neighboring candidate do not
qualify this release. The public `commercial-release-manifest.json` and final release receipt must
bind the exact commit/tree, artifact hashes, Core Gate results, Host token usage, 10k metrics,
platform matrix, supply-chain evidence, and public redownload hashes. Until those artifacts exist,
all corresponding outcomes remain `not_executed` or failed and this document makes no release,
superiority, or completeness claim.

Windows qualification bootstraps its duration weights from three deterministic, unweighted Python
3.12 shards. Their real JUnit receipts must aggregate into complete, disjoint coverage before the
workflow may derive weights or start the Python 3.11/3.13 weighted shards. The calibration shards
are retained separately from the nine final platform rows and cannot substitute for them.

Candidate-v2 10k timeout analysis found a full-Vault verification on every one of the 250 bounded
semantic batches. The qualification path now retains one verified public `KnowledgeOS` session
across the same 250×40 compilation runs. Batch dimensions, public seams, hard limits, and the final
pre-query verification are unchanged. This is an implementation correction, not a qualification or
pass claim.

Candidate-v3 failed the Windows Python 3.12 duration-calibration shard because four retained tests
assumed POSIX path or mode behavior and the exact-wheel version probe accepted LF but not the
equivalent Windows CRLF line ending. The test fixtures now use native path rendering and the
existing Windows private-file ACL hardener, while POSIX mode-bit assertions remain POSIX-only. The
version probe still requires exactly one expected UTF-8 line, no stderr, and now accepts either LF
or CRLF. Candidate-v3 remains a consumed failed candidate; these corrections require a new exact
candidate and make no platform-qualification or release-pass claim.

Calibration shards 1 and 3 were cancelled at the 100-minute job limit before they could retain
JUnit receipts. Their progress stream nevertheless contained failures, so timeout cannot be treated
as the only cause or as a pass. The calibration-only limit is now 150 minutes, and the later
duration-weighted final Windows platform shards now use the same 150-minute bound. Final Windows
shards stop at the first failure (`--maxfail=1`) to expose its traceback immediately; a green shard
still executes the complete selected inventory. Deterministically mapped failed nodes
are included in the fast Windows sentinel before another expensive candidate is admitted. One
confirmed OpenCode cause was the absolute-path scanner interpreting the drive suffix inside an
already verified `file:///C:/...` project-plugin URI as a separate path. Preflight now first verifies
the unique exact plugin URI, substitutes only that field in a structured copy, and applies the same
sensitive-value/path scan to every remaining resolved-config field. This does not allow arbitrary
drive, UNC, or other file paths into evidence.

The two remaining mapped Windows failures shared a separate pre-business-logic cause: their closed
Python child environments omitted Windows operating-system bootstrap variables, and importing
`asyncio` failed with `WinError 10106` before the public CLI or checkpoint recovery began. The
source-free public-seam diagnostic now copies only `SYSTEMROOT`, `WINDIR`, `COMSPEC`, and `PATHEXT`
when present, in addition to its existing fixed paths and locale. The checkpoint crash-recovery
fixture uses the shared closed subprocess-environment builder and then narrows `PATH` as before.
Provider credentials, ambient auth, test canaries, and arbitrary environment names remain excluded.

After that environment correction, the Windows 1k development diagnostic progressed into the real
public journey but exceeded its previous 900-second subprocess limit. Its Windows-only subprocess
bound is now 3,600 seconds inside the still-bounded 150-minute Candidate Full calibration job. The
fast Windows sentinel executes the separated environment-isolation contract and partial-checkpoint
recovery test; the full 1k journey remains in Candidate Full and cannot be replaced by the sentinel.

Candidate-v5 Candidate Full run #9 (`33149976563`) is consumed failed evidence, not a pass: the
Python 3.12 calibration and 10k lanes succeeded but do not replace the final platform matrix; all
six full Windows jobs were cancelled under the old 100-minute bound, and the Python 3.11 shard 3
failure signal was not parsed. A fresh Candidate Full run must validate the new bound and expose and
verify that failure; these notes do not claim the unknown failure is fixed.
