# DeepLaw v0.13 quality metric catalogue

Status: **definition only / source-free / claim-ineligible**.

This document explains the contract in [`contracts/v013-quality-metric-catalog.v1.schema.json`](../contracts/v013-quality-metric-catalog.v1.schema.json) and the exact machine-readable inventory in [`benchmarks/v013/quality-metric-catalog-v1.json`](../benchmarks/v013/quality-metric-catalog-v1.json). The catalogue defines what a later report must measure; it is not a report and contains no candidate observation, score, value, or pass/fail result.

## Closed inventory

The complete requirement list contains **44** metrics: 8 Retrieval + 5 Grounding + 8 Living Wiki
+ 8 Context + 7 Agent + 8 Authoritative. The complete display-name inventory is closed; adding an
item or silently dropping one is a contract change.

| Plane | Metrics |
| --- | --- |
| Retrieval (8) | Recall@K; User-visible Precision@K; Target Identity Precision; MRR; nDCG; Redundancy; Context Coverage; Duty Coverage |
| Grounding (5) | Statement Evidence Binding; Citation Validity; Unsupported Statement Rate; Source Coverage; Exact Quote Validity |
| Living Wiki (8) | Page Coverage; Link Completeness; Backlink Completeness; Orphan Rate; Gap Accuracy; Freshness Accuracy; Incremental Update Correctness; Projection Reproducibility |
| Context (8) | Compiled Hit; Targeted Fallback; Raw Fallback; Duplicate Evidence; RelevantChars/ContextChars; Provider Payload; Token Savings; Repeated-query Reuse |
| Agent (7) | Task Accuracy; Manual Correction; Tool Calls; Time; Tokens; Failure/Recovery; Cross-session Reuse |
| Authoritative (8) | Definition; Exception; Proviso; Temporal; Cross-reference; Wrong Version; Correct Gap; False Authority Admission |

Each display name has a stable, category-prefixed `metric_id` in the JSON artifact. The schema closes the objects and the 44-item array, and fixes the six category counts.

## Measurement contract

Every metric entry includes:

- a definition and numerator description;
- a denominator `unit`, exact `definition`, and `zero_denominator_semantics`;
- a `scope` that names the corpus, task, host, projection, and Authority plane;
- a closed-enum `gold_status` and a `direction` (`higher` or `lower`);
- `measurement_status` and explicit `not_executed_semantics`.

The current catalogue intentionally sets every metric to `gold_status=absent` and `measurement_status=not_executed`. No zero denominator is interpreted as zero, one, success, or failure. `review_pending` is reserved for a later review packet; it is not a measured result.

The catalogue is `source_free=true`, `claim_eligible=false`, and `competitive_claim_eligible=false`. It uses no user Vault, private legal or case material, network acquisition, model invocation, or external benchmark result. A source-free synthetic fixture may be named by a future report, but this definition artifact does not contain one.

## Requirements for a later measurement report

A formal measurement report must be a separate artifact. Before reporting any observation it must bind:

1. exact candidate bytes and revision;
2. exact corpus and Gold identity (including Gold status and digest);
3. host, model identity, projection profile, Authority plane, and policy configuration;
4. environment and run identifiers, including reproducible receipts;
5. the denominator population and zero-denominator decision for every metric.

The report must preserve `not_executed` or `review_pending` when execution or review is absent. It must not add a numerical result to this catalogue or convert a definition into a product-quality or comparative claim. `competitive_claim_eligible` remains false until the separate external metrics gates have real named, same-condition runs and all required evidence.
