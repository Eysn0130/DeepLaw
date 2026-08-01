# Semantic Living Wiki evaluation

Status: **vNext release gate**, 2026-08-01. This protocol is not evidence for v0.11.0 and is not a
comparative benchmark claim.

The frozen public corpus in `benchmarks/semantic/fixtures/` covers 15 named semantic cases. The
candidate labels, queries, purposes, expected objects, forbidden merges, lifecycle outcomes, fixture
hashes, hard failures, and limitations are jointly bound by
`benchmarks/semantic/semantic-gold-candidate-v1.json`. Formal scoring is closed until a maintainer
explicitly confirms that exact candidate with `benchmarks/semantic/review_gold.py`; the producing
Agent cannot confirm or score its own Gold.

## Real-host lifecycle

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

The v2 host report binds both prompts, the pinned command, every complete 15-duty Compilation Run,
the review/freshness receipts, final Vault verification, Gold status, and corpus identity. A failed
or skipped phase cannot be reported as passed. A deterministic fake-Agent exercises the protocol in
CI but never constitutes real-host/model evidence.

## Query and scoring gates

`benchmarks/semantic/run_query_suite.py` runs every maintainer-bound query twice through
`deeplaw knowledge query` using Query Plan v5 and a fixed public/personal budget. It records only
hashes, stable IDs, gap codes, bounded metrics, and latency; it does not persist query output text.
The gate checks explicit unanswerability, successor-only selection, withdrawal exclusion,
contradiction visibility, visible fallback, read-only behavior, Authority invariance, repeat reuse,
and the UTF-8 64 KiB provider limit. The attached cost receipt labels its token number as a UTF-8
byte proxy rather than a provider-token measurement.

`benchmarks/semantic/score_semantic_run.py` requires maintainer-confirmed Gold, a passed phased
real-host report, a passed first-party query report, zero hard failures, all deterministic quality
thresholds, and an explicitly recorded maintainer correction review. Formal release eligibility is
false if any one of these inputs is absent. `competitive_claim_eligible` remains false.

This protocol supports only the bounded product statement: any Agent that implements DeepLaw's
versioned CLI/MCP/Python contract and receives an explicit owner grant can use the same governed
Compilation transaction. It does not claim that every unknown host, model, plugin, or editor has
been executed.
