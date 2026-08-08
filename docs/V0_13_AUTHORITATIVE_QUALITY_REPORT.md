# DeepLaw v0.13 Authoritative Pack quality disposition

Status: **engineering capability accepted; independent critical-token review pending**.

This report is source-free. It binds public release metadata and immutable digests only. It does
not contain official prose, source paths, private material, or a maintainer/expert signature, and
it does not mutate an installed Authoritative Pack.

## Bound evidence

| Item | Exact value |
|---|---|
| Frozen public matrix | `benchmarks/quality/v0.11-28-source-decision-matrix.json` |
| Matrix Schema | `deeplaw.authoritative-source-quality-decision-matrix/v1` |
| Matrix record SHA-256 | `a81913e1d2f1b82dff08986db8834c67d476037a79cf8a5eb2df57d55f508abf` |
| Catalog | `deeplaw-cn-official`, sequence `2` |
| Catalog SHA-256 | `49cf75169726e18851897556617fad4132881614c3f6ab9c6b2a78d4f8524305` |
| Release | `lawrel_1bee97015ee440c71ea993b083a89005` |
| Release/database SHA-256 | `ff4bc58e3a77585dccb8b22bd049b50612b0a8c85f7fb858551ea424021fbdc0` |
| Public source identities | `28` |
| Sources with parser review warnings | `5` |
| Parser warning count | `32` |
| Review-required segment count | `8` |
| v0.13 disposition SHA-256 | `d49110c108099ee851a6acd332ccc844a245f11023bfe96351ccc275bc839c0f` |

`derive_review_dispositions` accepts only that exact public matrix record, exact 28-source
inventory, exact five warning-source identities, and exact warning/segment totals. A changed
record fails closed.

## Warning-specific disposition

| Stable source identity | Warnings | Review-required segments | Current v0.13 capability | Maintainer | Expert |
|---|---:|---:|---|---|---|
| `doc_003bce0e629646f4798dad04` | 8 | 1 | `identity_locator_only` | `maintainer_review_pending` | `expert_review_pending` |
| `doc_27744b8e4a30bea1d9e3f92f` | 4 | 3 | `identity_locator_only` | `maintainer_review_pending` | `expert_review_pending` |
| `doc_60224e01894c870874c413df` | 7 | 1 | `identity_locator_only` | `maintainer_review_pending` | `expert_review_pending` |
| `doc_9364963e345975e871203e53` | 4 | 2 | `identity_locator_only` | `maintainer_review_pending` | `expert_review_pending` |
| `doc_d63068d170e2015069276833` | 9 | 1 | `identity_locator_only` | `maintainer_review_pending` | `expert_review_pending` |

For all five records:

- `human_reviewed=false` and `expert_reviewed=false`;
- the Gap is `critical_token_review_pending` / `independent_review_unavailable`;
- exact-quotation and critical-token capabilities are not inferred from a whole-file hash;
- the capability is explicitly downgraded to identity/locator navigation;
- `pack_mutation_performed=false`, `release_mutation_performed=false`, and
  `official_prose_generated=false`.

This is the allowed L01 closure path: the risk remains visible and the affected capability is
downgraded. It is not a claim that article numbers, dates, amounts, negations, exceptions,
provisos, document numbers, amendments, or repeals were independently transcribed.

## Navigator and Authority boundary

The additive Authoritative Navigator is a deterministic read-only derived view. It exposes only
document/release/effective-date/segment/definition/cross-reference/warning/gap/receipt identities,
locators, capability states, and integrity digests. It does not open source bytes, generate
official prose, change Authority, make a legal-applicability decision, or import the general
Knowledge mutation coordinator.

Missing, partial, warned, unknown, or digest-invalid capability artifacts fail closed to
`identity_locator_only` or reject the view. A Companion Living Wiki interpretation remains
`origin=agent_derived` and `legal_authority=false`.

## Metric status

The frozen public v0.11 matrix records 37 retrieval cases (34 ranked), Hit@1
`0.9705882352941176`, MRR `0.9852941176470589`, receipt verification `1.0`, and required-evidence
duty coverage `68/138` (`0.4927536231884058`). Those values describe the exact frozen v0.11
release/database pair above. They are regression context, not a newly executed v0.13 28-source
quality gate and not a general precision or expert-quality claim.

The v0.13 four-plane metric disposition is:

| Plane | Denominator / scope | Gold state | v0.13 status |
|---|---|---|---|
| Document/Segment Retrieval | Exact frozen 28-source Pack and held-out target identities | required external Pack fixture unavailable in this construction workspace | `not_executed` |
| Evidence Duty Coverage | Applicable definition/exception/proviso/cross-reference/temporal duties | required external Pack fixture unavailable | `not_executed` |
| Citation/Temporal Correctness | Exact segment, version, effective date, receipt, wrong-version and OCR mutation cases | independent critical-token review absent | `review_pending` |
| Agent Interpretation Quality | Human/expert-reviewed companion interpretations | no signed human/expert Gold | `not_executed` |

The following command is therefore not reported as passed:

```bash
uv run python -m benchmarks.quality.run_authoritative_28_source_gate \
  --source-root <external-authoritative-source-root> \
  --catalog <signed-catalog.json> \
  --catalog-signature <signed-catalog.sig> \
  --rollback-catalog <older-signed-catalog.json> \
  --rollback-signature <older-signed-catalog.sig> \
  --output <v0.13-quality-report.json>
```

It requires exact external source bytes, signed catalogs and signatures that are intentionally not
stored in this repository. Substituting synthetic prose or the deterministic Navigator tests would
invalidate the gate.

## Release consequence

L01 is closed through an explicit, tested Evidence Capability downgrade; L02 and L04 have
source-free executable coverage. L03 remains `not_executed`/`review_pending`. No citation fallback,
false Authority, human review, expert review, Pack mutation, or competitive-quality claim is
asserted. Under the v0.13 qualification protocol this state is compatible only with continued
source-candidate review; it does not authorize an RC or v0.13.0 GA.
