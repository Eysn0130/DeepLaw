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
