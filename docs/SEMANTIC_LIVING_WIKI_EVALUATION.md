# Semantic Living Wiki evaluation

Status: **v0.12.0 release gate**, 2026-08-02. This protocol is not evidence for v0.11.0 and is not a
comparative benchmark claim.

> v0.13 source-candidate addendum: the historical mixed v1 compatibility report retains its Query
> Plan v5 retrieval command and remains a non-qualifying operator diagnostic. The current
> development workflow additionally emits
> `deeplaw.semantic-context-outcome/v2` for the default Query Plan v6 Context surface. It separates
> owner-local Capsule, Provider Capsule, complete MCP tool-result, selected content and transport
> metadata bytes; token estimates are named estimates, and token savings remain `not_executed`
> without a frozen equal-duty/equal-budget baseline. This visible, tuning-used report is not Human
> Gold, external qualification, or release evidence.

The frozen public corpus in `benchmarks/semantic/fixtures/` covers 15 named semantic cases and five
executed security challenges. The candidate labels, queries, purposes, expected objects, forbidden
merges, lifecycle outcomes, fixture hashes, hard failures, and limitations are jointly bound by
`benchmarks/semantic/semantic-gold-candidate-v1.json`. The complete freeze commitment is
`benchmarks/semantic/semantic-gold-freeze-v1.json`. It binds the candidate, fixture manifest,
Schema, query set, scoring policy, and security challenges independently by SHA-256.

The owner-approved release scope is deterministic machine consensus. The frozen policy records
`human_gold_review.status=not_required`, reason
`owner-approved deterministic machine-consensus release scope`, `maintainer_confirmed=false`, and
`reviewer_id=null`. It also records
`external_real_model_semantic_execution=not_executed` and
`competitive_claim_eligible=false`. No machine packet, derived Owner packet, or release artifact
may be described as human review. Formal release remains closed until all six isolated machine
auditors independently confirm every applicable gate for the exact candidate and the 28-source and
other release gates pass.

Entity and Concept labels use a **target-scoped** protocol, not a closed-world claim. Query-side
target precision applies the same rule to every labelled target: matching is constrained by the
frozen case Source Revision set and any claim-level content assertions. One stable Knowledge ID
matched inside that scope is a true positive; duplicate identities matched to the same target are
false positives; correct objects outside the source and claim scope are excluded from the
denominator. Extraction completeness and Source IR fragment coverage are reported separately. The
formal `source_coverage_definition` is bound to `source_ir_fragment_coverage`: covered Source IR
fragments divided by covered plus explicitly omitted fragments across every exact Compilation Run
in the compiler report. The query runner reads those counts from the real Ledger, binds the run-ID
set and Ledger audit head, and emits a hash-verified coverage receipt. The independent
`retrieval_source_coverage` metric instead measures distinct relevant Source Revisions selected by
at least one frozen query divided by all relevant Source Revisions referenced by the query set. The
two metrics are not interchangeable. These definitions are frozen in the Gold and Schema.

`compiled_hit_ratio` is defined only across cases with at least one required compiled Gold target.
Explicit-gap-only withdrawal and unanswerable cases are excluded, and a hit requires a labelled
target match; an inadmissible or unrelated compiled object cannot improve the ratio.

Same-condition comparisons use three baseline and three candidate repetitions in the frozen order
`baseline,candidate,candidate,baseline,baseline,candidate`. Adjacent alternating runs form three
ordered pairs. Deterministic quality and normalized provider-byte metrics compare the median of
three run-level values exactly. Latency p50/p95 use all 45 paired case observations; peak RSS uses
all supported paired query and challenge-process observations and reports the maximum. For those
performance statistics, 10,000 SHA-256-report-bound deterministic paired bootstrap resamples
produce a two-sided percentile 95% interval for candidate-minus-baseline. A regression is detected
only when the complete interval is worse than zero; there is no effect-size tolerance. Raw run
values, observed delta, interval, and sample count remain visible, so this is explicitly a
statistical regression-detection rule rather than a claim that noisy point estimates are identical.
Peak RSS is sampled directly from each first-party CLI process; audit-helper processes and the
benchmark orchestrator are excluded. Linux uses `/proc/<pid>/status`, macOS uses `ps`, and an
unsupported platform records `null` rather than substituting a process-wide proxy.

Source Summary, cross-source Synthesis, and Overview labels include claim-level required content,
expected entailment, and exact source-key sets. Deterministic required-term/source checks are only a
precheck; structural citation validity is scored separately. Query Plan v5 exposes a hash-bound
Synthesis evidence receipt for multi-source Syntheses, and the precheck resolves every required
term against its exact provider-visible Source Revision, fragment, locator, and quote hash. A
single-source Source Summary or Overview uses its exact canonical source ref without duplicating a
receipt. The bilingual derived Owner Review Packets retain the full claims and claim-to-evidence
checks for inspection. Their human decision remains `not_required`; they do not alter canonical
Gold.

Fourteen cases also freeze a natural-Chinese query variant; the identifier-only
`semantic-case-13` remains language-neutral. Every variant reuses the canonical case's exact
target-scoped objects, claim assertions, citations, source coverage, safety rules, and provider
budget. The runner executes each variant cold and warm through the first-party CLI and records a
separate pass rate, provider-visible byte total, hard-limit count, stable IDs, Query Plan, evidence
binding, and process RSS. A failed variant fails its canonical case and the whole run.

Cross-language discovery uses the bounded query-only profile
`deeplaw-deterministic-query-expansion/1`. Its phrase aliases are never written to source bytes,
Source IR, Knowledge identity, Markdown, or indexes. Query Plan v5 records only the profile,
application flag, term count, and terms digest together with explicit `authority_changed=false`
and `stored_evidence_changed=false`; the internal autonomous plan binds the same facts under
`deeplaw.autonomous-query-plan/v1`. Expansion can propose candidates but cannot admit content,
alter temporal or sensitivity policy, elevate Authority, or substitute translated text for exact
evidence.

Multilingual timeline queries preserve exact ISO-date anchors through the deterministic relevance
floor and order selected Events by valid time. Mixed Event/Concept queries retain exact identity
matches alongside the purpose-selected kind set. Exact-identity discovery does not globally admit
unrelated low-score candidates. These rules keep target extraction complete without counting valid
out-of-scope objects as false positives.

Target-scoped freshness checks first resolve a bounded exact Knowledge identity and fall back to
the requested lexical/dense mode only when no exact result exists. They skip graph expansion because
dependency freshness is evaluated from canonical bindings rather than neighboring knowledge. The
requested graph-hop budget remains visible as a maximum in Query Plan v5 even when no hop is used.

## Deterministic pre-review lifecycle

`benchmarks/semantic/run_deterministic_lifecycle.py` is an offline, fixture-specific no-model Agent.
It exercises the real closed Compilation Packet/Observation/Publication/commit protocol, lifecycle
transitions, Ledger, Source Revisions, Knowledge Revisions, and first-party verification. It is
review evidence, not external-model evidence. In case 01 the source produces at least two distinct
Packets, each contributes a governed observation for the same entity, and finalization must map the
observations to one stable Knowledge ID.

`benchmarks/semantic/run_query_suite.py` then executes all 15 frozen cases through the first-party
`deeplaw knowledge query`, `context`, `source fragment`, and `verify-capsule` surfaces. It also
executes prompt-injection, unsupported-Authority, restricted-disclosure, unauthorized-mutation, and
silent-fallback challenges. A challenge that did not execute cannot contribute a zero failure
count. Exact Source Revision, fragment, locator, hash, evidence receipt, Query Plan, UTF-8 budget,
real cursor continuation and exact reassembly, cold/warm latency, and repeated-query reuse are
retained in schema-valid reports.
The query scorer also reads the real Observation/Disposition rows for case 01, requires matching
entity observations from at least two distinct Packet IDs, and binds their single disposition
target to exactly one final stable Knowledge ID in a hash-verified receipt.
The report also freezes the exact Gold/fixture/Source Revision/query digests, budgets, Query Plan,
FTS/dense/reranker/graph identities, OS, hardware, first-party command runtime Python, SQLite,
dependency-inventory digest, network policy, and the precise cold/warm definitions. Environment
identity comes from the measured CLI runtime, not the benchmark orchestrator. It reports
compiled-hit/fallback ratios, uncompiled-source count, extraction completeness, retrieval source
coverage, Ledger-derived Source IR fragment coverage and counts, evidence attachment, peak RSS,
and bytes per matched target.
`benchmarks/semantic/compare_query_runs.py` provides an exact single-run diagnostic.
`benchmarks/semantic/compare_query_replicates.py` is the release gate: it rejects changed
conditions, a candidate report failure, any deterministic quality or efficiency regression, any
statistically detected performance regression under the frozen method, or any security failure.

`benchmarks/semantic/build_machine_review_consensus.py` validates six source-free, schema-valid
machine-review packets, rejects duplicate or missing roles and any `RETURN_FOR_FIX`, `BLOCKED`, or
cross-packet discrepancy, and emits a consensus record plus English and Chinese derived Owner
Review Packets. Each packet binds exact commit/tree/version, canonical Gold, fixture manifest,
Schema, frozen query, actual IDs/claims/citations, Query Plan, commands, and evidence digests. The
Chinese auditor must reproduce every frozen natural-Chinese variant (and the language-neutral
identifier case); all other roles must reproduce every canonical English query. No Agent may
populate a human reviewer identity or
`maintainer_confirmed`.

Claude Code and OpenCode pre-review reports record exact pinned versions and actual discovery,
authentication, model-access, and non-execution reasons. They are
`external_real_model_semantic_execution=not_executed`; no-model discovery is never represented as
real-model validation.

## Optional external real-host lifecycle

`benchmarks/semantic/prepare_host_corpus.py` uses only the first-party CLI to create a fresh Vault,
ingest and review the public fixtures, create a least-privilege semantic-compiler grant, retain
`update-v2` as an exact pending successor, and create a verified pre-run snapshot. Its v2 corpus
receipt contains stable labels and governed IDs but no source text or private path.

`benchmarks/hosts/run_semantic_host_harness.py` can execute the following real-host phases outside
the formal v0.12 deterministic release scope:

1. the real Agent compiles every active baseline Source Revision through the versioned Skill and MCP
   protocol;
2. deterministic owner CLI operations activate the frozen successor and refresh predecessor
   freshness;
3. the same host/model compiles the exact successor and refreshes admissible Synthesis work;
4. deterministic owner CLI operations withdraw `retention-a` and refresh its dependants.

When explicitly run, the v2 host report binds the exact clean repository
commit/tree/version/lock/contract/migration
inventory, both prompts, the pinned command, provider-reported `turn.completed` build tokens, every
complete 15-duty Compilation Run, the review/freshness receipts, final Vault verification, Gold
status, and corpus identity. A failed
or skipped phase cannot be reported as passed. A deterministic fake-Agent exercises the protocol in
CI but never constitutes real-host/model evidence.

## Query and scoring gates

The query suite runs every canonical frozen query and every applicable frozen Chinese variant
twice using Query Plan v5 and a fixed public/personal budget. It records hashes, stable IDs, gap
codes, bounded evidence receipts, actual metrics, and latency; it does not persist private source
text. The gate checks explicit unanswerability,
successor-only selection, withdrawal exclusion, contradiction applicability, visible fallback,
read-only behavior, Authority invariance, repeat reuse, pagination, and the UTF-8 64 KiB provider
limit. The historical v1 cost receipt labels its token number as a UTF-8 byte proxy rather than a
provider-token measurement. The additive v2 cost receipt does not expose `total_query_tokens`: it
reports Context delivery byte boundaries separately, records an explicitly named token estimate,
leaves actual Provider input tokens null without a usage receipt, and keeps token savings
`not_executed` without a valid comparator.

`benchmarks/semantic/score_semantic_run.py` remains the contract for an optional external real-host
experiment; it is not used to imply external-model evidence in this release. Formal v0.12 semantic
eligibility instead requires a passed deterministic lifecycle, passed first-party query/cost
reports, six unanimous isolated machine-review packets, a validated consensus record, bilingual
derived Owner packets with no human reviewer, and the exact owner policy above. Any discrepancy
invalidates the complete six-packet set for that candidate. `competitive_claim_eligible` remains
false.

`.github/workflows/semantic-evidence.yml` has two credential-free modes. `deterministic_review`
builds a fresh wheel, produces deterministic lifecycle, v5 diagnostic, v6 Context outcome and v2
cost evidence, and emits truthful
Claude Code/OpenCode `not_executed` reports. `package_consensus` checks out the exact clean
candidate and a separate sanitized evidence ref, validates the six read-only audit packets and
28-source matrix, builds the consensus and bilingual Owner packets, scans for private paths and
secret material, and emits `semantic-release-evidence`. Neither mode calls a model Provider or
accepts an API key. Existing commercial assembly that requires the historical cost contract remains
fail closed; the v2 development report does not enable release assembly.

This protocol supports only the bounded product statement: any Agent that implements DeepLaw's
versioned CLI/MCP/Python contract and receives an explicit owner grant can use the same governed
Compilation transaction. It does not claim that every unknown host, model, plugin, or editor has
been executed.
