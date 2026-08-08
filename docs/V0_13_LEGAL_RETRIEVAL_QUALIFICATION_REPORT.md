# DeepLaw v0.13 legal-retrieval qualification disposition

Status: **local deterministic regressions passed; exact signed 28-source qualification not
executed** (2026-08-08). This report does not establish legal correctness, Pack quality, RC, GA,
or a competitive claim.

## Boundary and executed evidence

`law_support` remains a separate read-only process/store and the local tests used only repository
fixtures. No client/case facts, private legal material, live user Vault, credential, network source,
or unsigned substitute was used. Agent-derived companion interpretation remains
`origin=agent_derived` and `legal_authority=false`.

The executed command was:

```bash
uv run --frozen pytest -q \
  tests/test_legal_quality.py \
  tests/test_legal_topics.py \
  tests/test_v013_authoritative_navigator.py \
  tests/test_extract_segment.py \
  tests/test_release_search.py
```

Result: **86 passed**. These regressions cover the current local document/segment search,
topic/definition lookup, source-free Authoritative Navigator, extraction locator behavior and
release search boundary. They are development evidence only; they do not use the exact external
signed Pack and do not produce Human Gold retrieval metrics.

Current bindings:

| Artifact | SHA-256 |
| --- | --- |
| `benchmarks/quality/run_authoritative_28_source_gate.py` | `db729873d9c4985b5bbf777544f51a98ef1ed9eb9f63c50303303669b5f2395e` |
| `tests/test_legal_quality.py` | `6ee2d84b880c913d8dd0c81a508abd3d2c811719fc7a522c732a545243aa6939` |
| `tests/test_legal_topics.py` | `ff8db13de6252a5a57450297f9520725bd75d09be7cf314fa83b9675299d1473` |
| `tests/test_v013_authoritative_navigator.py` | `6ab7b84c880b8a22895d245e6b18f0b0001b2b9cc8a47a43454e59a1f09c7ffc` |
| `tests/test_extract_segment.py` | `aa8ce4708f1e6d953733434d5c434df4cb483f7eeecb663c9a310e1da85bed0d` |
| `tests/test_release_search.py` | `e85977ddf14bf35d1ae3f9fc30732a7f1f57d59c954697550a966c20bb184fd7` |

## Frozen legal metrics

The exact metric names, thresholds, zero-denominator behavior and hard failures are frozen in
`benchmarks/v013/qualification-protocol-v1.json`. The protocol SHA-256 is
`95283e2d1fdd60a429941c6ab718cebd739ad414ddc38d58b3f2fcc14f4cffb5`.

| Required measurement | Status | Reason |
| --- | --- | --- |
| Document Recall@K | `not_executed` | Exact Pack corpus and Human Gold are absent. |
| Exact Segment Recall@K | `not_executed` | Exact Pack corpus and Human Gold are absent. |
| Target Identity Precision, MRR, nDCG | `not_executed` | No frozen external query/identity labels were supplied. |
| Definition/Exception/Proviso/Cross-reference Recall | `not_executed` | No evaluator-isolated duty Gold was supplied. |
| Temporal Correctness / Wrong-version Inclusion | `not_executed` | Signed version chain, `as_of` labels and wrong-version distractors are absent. |
| Citation Validity / invalid Quote or Locator | `not_executed` | Local contract regressions pass, but the exact Pack hard-failure count was not measured. |
| Correct Gap Precision/Recall | `not_executed` | No frozen no-answer/conflict Gold was supplied. |
| False Authority Admission | `not_executed` | Local Authority-negative tests pass; exact Pack metric remains unmeasured. |
| Redundancy / RelevantChars-to-ContextChars | `not_executed` | No scored Pack capsules were generated. |
| OCR critical-token mutations | `not_executed` | No independently reviewed amount/date/section/negation/exception mutation set was supplied. |

The required hard-failure target is zero for False Authority, invalid Quote/Locator and a wrong
version admitted as primary evidence. An absent denominator is not interpreted as zero and local
unit coverage is not substituted for the missing exact-Pack observation.

## Missing exact gate

No signed catalog, signature, exact 28 source bytes, rollback catalog/signature, frozen Pack Gold,
or independent critical-token reviewer was available in the authorized inputs. The exact future
command remains:

```bash
uv run --frozen python -m benchmarks.quality.run_authoritative_28_source_gate \
  --source-root <external-authoritative-source-root> \
  --catalog <signed-catalog.json> \
  --catalog-signature <signed-catalog.sig> \
  --rollback-catalog <older-signed-catalog.json> \
  --rollback-signature <older-signed-catalog.sig> \
  --output <v0.13-quality-report.json>
```

The placeholders are mandatory external inputs, not values to infer from repository fixtures.
Accordingly `exact_28_source_gate=not_executed`, `human_gold=review_pending`,
`legal_release_gate_passed=false`, and `competitive_claim_eligible=false`.
