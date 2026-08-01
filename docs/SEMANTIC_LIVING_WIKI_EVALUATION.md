# Semantic Living Wiki evaluation

Status: **v0.12.0 release gate**, 2026-08-01. This protocol is not evidence for v0.11.0 and is not a
comparative benchmark claim.

The frozen public corpus in `benchmarks/semantic/fixtures/` covers 15 named semantic cases and five
executed security challenges. The candidate labels, queries, purposes, expected objects, forbidden
merges, lifecycle outcomes, fixture hashes, hard failures, and limitations are jointly bound by
`benchmarks/semantic/semantic-gold-candidate-v1.json`. The complete freeze commitment is
`benchmarks/semantic/semantic-gold-freeze-v1.json`. It binds the candidate, fixture manifest,
Schema, query set, scoring policy, and security challenges independently by SHA-256.

The current candidate remains `maintainer_review_pending`: `review=null` and the freeze has no
`reviewer_id`. Formal scoring, paid external-model execution, merge, tag, and Release are closed
until an independent retrieval audit has completed and the owner explicitly confirms this exact
Gold. The producing Agent and the audit Agent cannot confirm, sign, or score their own Gold as
human-reviewed evidence.

Entity and Concept labels use a **target-scoped** protocol, not a closed-world claim. Query-side
target precision applies the same rule to every labelled target: matching is constrained by the
frozen case Source Revision set and any claim-level content assertions. One stable Knowledge ID
matched inside that scope is a true positive; duplicate identities matched to the same target are
false positives; correct objects outside the source and claim scope are excluded from the
denominator. Extraction completeness and Source IR fragment coverage are reported separately. This
definition is frozen in the Gold and Schema.

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
receipt. The Human Review Packet
retains the full claims and claim-to-evidence checks for independent and owner review.

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
pagination, cold/warm latency, and repeated-query reuse are retained in schema-valid reports.
The report also freezes the exact Gold/fixture/Source Revision/query digests, budgets, Query Plan,
FTS/dense/reranker/graph identities, OS, hardware, Python, SQLite, network policy, and the precise
cold/warm definitions. It reports compiled-hit/fallback ratios, uncompiled-source count, extraction
completeness, retrieval source coverage, evidence attachment, peak RSS, and bytes per matched target.
`benchmarks/semantic/compare_query_runs.py` provides an exact single-run diagnostic.
`benchmarks/semantic/compare_query_replicates.py` is the release gate: it rejects changed
conditions, a candidate report failure, any deterministic quality or efficiency regression, any
statistically detected performance regression under the frozen method, or any security failure.

`benchmarks/semantic/export_human_review_packet.py` exports a source-free 15-case JSON and Markdown
matrix. Machine precheck, independent-auditor recommendation, and owner decision are separate
fields. Both auditor and owner fields are unset when generated; no Agent may populate the human
decision or `maintainer_confirmed` state.

Claude Code and OpenCode pre-review reports record exact pinned versions and actual discovery,
authentication, model-access, and non-execution reasons. They are
`external_real_model_semantic_execution=not_executed`; no-model discovery is never represented as
real-model validation.

## External real-host lifecycle

`benchmarks/semantic/prepare_host_corpus.py` uses only the first-party CLI to create a fresh Vault,
ingest and review the public fixtures, create a least-privilege semantic-compiler grant, retain
`update-v2` as an exact pending successor, and create a verified pre-run snapshot. Its v2 corpus
receipt contains stable labels and governed IDs but no source text or private path.

`benchmarks/hosts/run_semantic_host_harness.py` then executes two real-host phases:

1. the real Agent compiles every active baseline Source Revision through the versioned Skill and MCP
   protocol;
2. deterministic owner CLI operations activate the frozen successor and refresh predecessor
   freshness;
3. the same host/model compiles the exact successor and refreshes admissible Synthesis work;
4. deterministic owner CLI operations withdraw `retention-a` and refresh its dependants.

After owner confirmation, the v2 host report binds the exact clean repository
commit/tree/version/lock/contract/migration
inventory, both prompts, the pinned command, provider-reported `turn.completed` build tokens, every
complete 15-duty Compilation Run, the review/freshness receipts, final Vault verification, Gold
status, and corpus identity. A failed
or skipped phase cannot be reported as passed. A deterministic fake-Agent exercises the protocol in
CI but never constitutes real-host/model evidence.

## Query and scoring gates

The query suite runs every frozen query twice using Query Plan v5 and a fixed public/personal
budget. It records hashes, stable IDs, gap codes, bounded evidence receipts, actual metrics, and
latency; it does not persist private source text. The gate checks explicit unanswerability,
successor-only selection, withdrawal exclusion, contradiction applicability, visible fallback,
read-only behavior, Authority invariance, repeat reuse, pagination, and the UTF-8 64 KiB provider
limit. The attached cost receipt labels its token number as a UTF-8 byte proxy rather than a
provider-token measurement.

`benchmarks/semantic/score_semantic_run.py` requires maintainer-confirmed Gold, a passed phased
real-host report, a passed first-party query report, zero hard failures, all deterministic quality
thresholds, and an explicitly recorded maintainer correction review. Formal release eligibility is
false if any one of these inputs is absent. `competitive_claim_eligible` remains false.

`.github/workflows/semantic-evidence.yml` has a credential-free `deterministic_review` mode that
builds a fresh wheel, produces the deterministic lifecycle/query/Human Review Packet artifacts, and
emits truthful Claude Code/OpenCode `not_executed` reports. It deliberately does not call a model
Provider. External execution still uses two workflow runs. `execute` uses the
externally managed `OPENAI_API_KEY` secret without printing or packaging it, uploads a private
review candidate, and cannot produce formal evidence. After the maintainer reviews that exact
report and derived Wiki, `finalize` binds correction count/time and reviewer identity to the same
Vault/report and emits `semantic-release-evidence`. The existing release workflow accepts only that
artifact for the exact release commit; the artifact never contains the review Vault or credentials.

If the owner explicitly limits v0.12.0 to deterministic retrieval, governance, security, and human
Gold review, the manifest may record external real-model execution under `Not verified` and
`Not claimed`; otherwise the existing external real-host gate remains mandatory. In both scopes,
`competitive_claim_eligible=false`.

This protocol supports only the bounded product statement: any Agent that implements DeepLaw's
versioned CLI/MCP/Python contract and receives an explicit owner grant can use the same governed
Compilation transaction. It does not claim that every unknown host, model, plugin, or editor has
been executed.
