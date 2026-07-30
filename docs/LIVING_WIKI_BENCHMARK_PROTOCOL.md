# Living Wiki comparative benchmark protocol

Status: **Preregistered, not executed**, 2026-07-30.

The machine-readable protocol is
[`benchmarks/living_wiki/protocol-v1.json`](../benchmarks/living_wiki/protocol-v1.json); public
development fixtures are
[`benchmarks/living_wiki/fixtures-v1.json`](../benchmarks/living_wiki/fixtures-v1.json). Both have
closed JSON Schemas and contract tests.

## Required run binding

Every comparative run must freeze the exact repository commit, release version, wheel digest,
contract inventory, migration identity, corpus digest, source-update sequence, split, host model,
prompt digest, permissions, context/token budgets, hardware, network policy, build/query cost and
complete failure inventory. Case-level outputs and paired uncertainty are mandatory.

## Named comparators

The preregistration contains exactly:

1. Guanlan;
2. DeepLaw `compiled-first-v1`;
3. DeepLaw source-fragment fallback;
4. a named traditional RAG implementation;
5. a named pure embedding implementation;
6. a named GraphRAG implementation;
7. Tolaria with an exact Agent, release, prompt and permission mode;
8. Obsidian with an exact named AI plugin, version, model, prompt and workflow.

Obsidian core is not described as an LLM Wiki compiler. Tolaria is not compared without fixing its
Agent workflow. Every comparator currently has `status=not_executed`.

## Metrics and failures

The protocol measures identity deduplication/ambiguity, concept fusion, source coverage, claim
binding, contradictions, synthesis freshness, stale/withdrawal prevention, update propagation,
groundedness, citation validity, compiled/fallback ratios, first/incremental/query latency,
build/query tokens, rebuild consistency, multi-Agent reuse, manual correction cost and
failure/recovery rate.

Hard failures include wrong merges, duplicate canonical identity, unsupported claims, invalid
citations, stale/withdrawn/restricted admission, unauthorized mutation, Authority elevation,
partial publication presented as complete, unbounded payload and unrecoverable Runs.

Fixtures include same-name and multi-alias entities, conflicting sources, successors, withdrawal,
permission changes, long and multi-format documents, more than 300 objects, repeated queries,
exact quotations, historical versions and unanswerable questions. Generator-labelled cases define
future deterministic fixture materialization; they are not fabricated benchmark results.

## Claim gate

```text
competitive_claim_eligible=false
```

This remains false until every named available comparator is actually run under the same frozen
conditions and complete results are retained. The protocol forbids claims such as “best”,
“leading”, “superior to Guanlan/Obsidian/Tolaria” and “SOTA” without that evidence.

Real host tasks are separate from no-model lifecycle and fake-Agent CI. Use
[`run_living_wiki_host_harness.py`](../benchmarks/hosts/run_living_wiki_host_harness.py); without
explicit execution it produces a schema-valid `not_executed` report.
