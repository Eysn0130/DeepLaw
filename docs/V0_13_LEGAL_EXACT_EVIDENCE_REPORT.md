# DeepLaw v0.13 legal exact-evidence development report

Status: **not eligible / development thresholds failed**. The legal plane remains read-only and
isolated. No private case data, Provider, credential, or network source was used.

Implementation freeze: `450e79e66a30399385ab4afd2d137414e78b7119`.

## Frozen task

| Input | SHA-256 | Status |
| --- | --- | --- |
| Synthetic two-version source | `a0b38dfffe57601fec4667603305f095069c756e4f70a822840c8a1137f4c755` | development only |
| Owner-task legal Gold | `fd37c1a517fad92d46d648593725108ad5536b6dc814969af271e1898d559a6c` | second legal human review not executed |

The source contains a 2026 current document with an exact-evidence duty and exception, plus a 2030
future version. The candidate built a temporary unsigned development release, then used read-only
search/get/verify/capability/citation-audit interfaces. The candidate was denied network access,
Gold, and the scorer; the scorer was denied source and candidate code.

The synthetic manifest uses the official collection runtime solely because `law_support` enforces
that collection contract. The candidate explicitly records `development_only=true`,
`signed=false`, `official_claimed=false`, and `release_claimed=false`; this is not an official Pack.

## Result

The 2030 future version was excluded at the 2026 `as_of` date and returned an explicit temporal
gap. Authority partitions remained separate: official evidence retained `legal_authority=true`,
while the Agent interpretation retained `origin=agent_derived` and `legal_authority=false`.

However, the unsigned development release could not establish verified temporal capability for
the two expected 2026 primary-evidence cases. Both the current duty and exception returned no
primary evidence with `temporal_metadata_unverified`/`no_primary_evidence` gaps. Consequently:

| Metric | Result |
| --- | ---: |
| Document Recall | 0.0 |
| Exact Segment Recall | 0.0 |
| Target Identity Precision | 0.0 |
| MRR / nDCG | 0.0 / 0.0 |
| Exception Recall | 0.0 |
| Temporal Correctness | 1.0 |
| Correct Gap Precision / Recall | 1.0 / 1.0 |
| False Authority Admission | 0 |
| Invalid Quote/Locator primary evidence | 0 |
| Wrong-version primary evidence | 0 |

`Citation Validity=1.0` in the machine report is vacuous for the two required cases because no
primary citation was admitted; it must not be described as successful exact-citation coverage.
Focused regression fixtures separately exercise quote, quote-hash, locator, receipt, Source hash,
segment hash, and version tampering, but they do not replace execution of the frozen legal task.

Final-run hashes:

| Artifact | SHA-256 |
| --- | --- |
| Legal candidate | `de8e6b16e5b909306e8260e726337348981788e46c7054972211cd4ffae6ecfd` |
| Legal score | `7a2bf2ee3b65002e2a3ac667a9d7cd1a583b8cf7ccdc3766167943b21ff04e78` |

## Required next evidence

Do not weaken temporal admission or mark the synthetic release signed. Re-run the same source-only
candidate against an exact signed 28-source Pack or a properly licensed, hash-frozen, independently
reviewed alternative whose temporal metadata can be verified. The next legal Gold must receive an
independent legal human review before it can satisfy the gate.

Not executed: signed 28-source Pack, independent legal Human Gold, critical-token human review,
real Codex, cross-platform fresh artifact, public redownload, or release provenance.

`development_thresholds_passed=false`

`release_gate_passed=false`

`claim_eligible=false`

`competitive_claim_eligible=false`
