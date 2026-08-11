# DeepLaw v0.13 Pass 11 Wiki and professional Evidence disposition

Status: **exact-candidate local development evidence; qualification incomplete** (2026-08-11).
Package remains `0.12.0`; `release_ready=false`.

This disposition separates executed local evidence from missing Human, external, legal, platform,
and release evidence. It does not turn a synthetic Vault, deterministic test, screenshot, or scale
construction diagnostic into a qualification result.

## Exact bindings

| Evidence | Candidate | Result |
| --- | --- | --- |
| Obsidian Desktop | commit `d2f4f518cf3b0abcaf876f97d34b2fea7951c7af`, tree `a304e221186205f515242b618272ff0b75e57f03`, wheel SHA-256 `ce5631098d331325c909275d3fa6db788e0630a6b165cf82408c5922db89e33a` | executed synthetic macOS seam; claim-ineligible |
| Wiki/performance scale | commit `69db28cb99846540e3b7c3c600f5268705405015`, tree `a32e3218658403376cfbf9340972f9aa0caab177`, same wheel | 1k/10k/100k fixtures constructed; partial gates remain unexecuted |
| Source/Wiki/Evidence regressions | commit `ab9d1dc481d74a905f690ffdc42f869a6353a11d`, tree `931b07dbef359506ca8b5a7f24a896f18a74f9e6` | 140 focused professional-Evidence tests plus 74 Wiki/source-lifecycle tests passed |
| Tolaria source compatibility | release tag `v2026-08-11`, annotated-tag commit `cb45f26649a7500e0bdb5dd0b8f0412e9c1daf4d`, package `0.1.0` | source/hash/contract review only; Desktop seam not executed |

The retained machine-readable scale artifacts and their digest manifest are under
[`../benchmarks/v013/evidence/pass11-wiki-evidence-scale-2026-08-11/`](../benchmarks/v013/evidence/pass11-wiki-evidence-scale-2026-08-11/).
The Obsidian receipt, six inspected synthetic captures, and their digest manifest are under
[`../benchmarks/hosts/evidence/pass11-obsidian-desktop-2026-08-11/`](../benchmarks/hosts/evidence/pass11-obsidian-desktop-2026-08-11/).

## Living Wiki and editor tasks

The exact Obsidian bundle loaded in Obsidian Desktop `1.13.4`. The real command palette verified
the synthetic Vault. A governed Markdown file was renamed and edited in the Desktop UI, then
reconciled through the shared DeepLaw coordinator. Stable Knowledge identity survived the path
change and a new attributable revision was created. A later stale Desktop edit produced an
explicit `stale_base_revision` conflict; the current head was restored and no silent merge or audit
rewind occurred. The previous user Vault was restored after the run, and no user content or local
path was retained in the receipt or captures.

Deterministic current-candidate tests additionally covered alias ambiguity, same-name/wrong-merge
rejection, backlinks/outlinks, source successor and moved-fragment behavior, user-file protection,
projection recovery, resolver/registry identity, and the 1k full/incremental equivalence check.
These are development observations. An independently timed Human and Agent finding the same
knowledge, broader accessibility/usability review, and a blind editor task remain `not_executed`.

Tolaria was not installed. The current release source pin and unchanged reviewed file hashes were
verified, but open/edit/external-change/reconcile through its Desktop UI was not executed. Its
status remains `integration_limited`; the source harness is not a substitute for the missing UI
extension seam.

## Scale observations

The performance report contains 63 operation/scale observations: 48 executed, 17 passed a frozen
threshold, zero failed, and 15 remained `not_executed`. “Executed” is not restated as “passed.”

| Scale | Wiki page max | Backlinks max | Full rebuild | Persistent startup | SQLite storage |
| ---: | ---: | ---: | ---: | ---: | ---: |
| 1k | 81.424 ms | 77.572 ms | 257.813 ms | cold 219.139 ms / warm 42.506 ms | 16,400,384 bytes |
| 10k | 585.652 ms | 587.955 ms | 1,818 ms | not executed | 74,559,488 bytes |
| 100k | 6,962.963 ms | 7,022.486 ms | 17,211.561 ms | not executed | 740,601,856 bytes |

Provider payload maximum remained 13,311 bytes in this runner, below the hard safety ceiling. The
1k lane executed source update, cache invalidation, exact full/incremental projection equivalence,
and persistent MCP cold/warm probes. The 10k/100k source-update/equivalence, persistent MCP,
cache-invalidation, and isolated RSS-stability probes remain `not_executed`.

The separate Statement runner constructed 1k/10k/100k public compilation fixtures. All three
Statement lanes retained tail recall, position independence, the 512-candidate bound, and Provider
payloads below 7,061 bytes. Snapshot/restore was executed and valid only at 1k. Relation lanes at
all three scales remain `not_executed`: the public `add_relation` mutation is rate-bounded and no
safe audited bulk constructor exists. No private bulk API was added to manufacture a result.

The query/graph report remains byte-for-byte bound to candidate `69db28c`. It explicitly records
that the synthetic scale construction did not read or score the repository-visible development
Gold. After the Pass 11 documentation changed, the development Gold was rotated, and the later
Windows ACL fix changed one source file bound by the runner, verification from the current checkout
correctly returns `Gold byte binding mismatch` and `candidate source byte binding mismatch`. The
historical report and its manifest were not rebound or rewritten; its measurements are retained as
historical candidate observations and are not current qualification evidence.

## Professional Evidence boundary

The exact clean candidate ran:

```text
140 passed in 25.78s
74 passed
```

The focused suites exercise Markdown and HTML Source IR, DOCX footnote/list/endnote preservation,
PDF native-quality/render/OCR fail-closed behavior, OCR critical-token consensus, exact
Source Revision/fragment/Locator/quote materialization, wrong-version rejection, explicit Gap,
Wiki-to-Source resolution, source successor behavior, and immutable/user-owned file boundaries.
They use repository synthetic fixtures and temporary files only. They do not establish usability or
fidelity on a licensed professional corpus, and no editable Wiki copy is treated as original
evidence.

The retained synthetic legal report remains a failure observation: Document Recall, Exact Segment
Recall, and Exception Recall are all `0.0`. Temporal admission and Authority hard-zero gates were
not weakened. No exact licensed/signed corpus or independent legal reviewer was supplied, so legal
qualification remains blocked and no replacement evidence was created.

## Unmet gates

- independent Human Wiki and professional-document tasks;
- Tolaria Desktop/UI execution;
- 10k/100k incremental equivalence, persistent MCP, cache invalidation, and isolated RSS stability;
- Relation scale and truncation evidence through a public audited seam;
- 10k/100k snapshot/restore;
- licensed/signed legal corpus, independent legal Gold, and critical-token reviewer;
- Linux and Windows execution, final artifact chain, signature, public redownload, and final blind.

```text
claim_eligible=false
competitive_claim_eligible=false
release_gate_passed=false
release_ready=false
package_version=0.12.0
```
