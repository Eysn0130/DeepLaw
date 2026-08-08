# DeepLaw v0.13 P0 reproduction and remediation report

Status: **source-candidate defects reproduced and minimally remediated; external release gates
remain unmet** (2026-08-08). `pass` in this document means only the named local regression passed.

## Scope and decision rules

This work is a current source-candidate fix, not a v0.13 reimplementation or release. Each change
was preceded by a public-seam or deterministic reproduction. Unreproduced cases remain explicit
`not_executed`/skips rather than manufactured private-database fixtures. Package version remains
`0.12.0`.

## P0 reproductions and root causes

| Area | Reproduction | Root cause / impact | Disposition |
| --- | --- | --- | --- |
| Host credential inheritance | An ambient canary entered all three old Host paths through `os.environ.copy()` and was inherited by a fake MCP child. | Whole-process environment inheritance could disclose an unrelated ambient or Provider Secret. P0 confidentiality boundary failure. | Fixed with a closed allowlist and isolated per-invocation HOME/XDG/temp/cwd. |
| Query v6 Statement tail | A public Profile-v3 fixture with 5,001 Statements missed the exact final Statement. | `_MAX_STATEMENT_SCAN=5000` selected a global ordered prefix before matching, making table position a retrieval signal. | Fixed: governed discovery selects at most 20 revisions; SQL matches only those revisions into a 512-candidate pool and orders an exact phrase first. |
| Query v6 controls | Changing `graph_hops`, `retrieval_mode` and `force_canonical_lexical` did not change v6 because the executor deleted them. Invalid retrieval modes could reach the shared Python seam. | Public inputs were silently accepted and discarded; plan/receipt could not explain actual discovery. | Fixed and retained in v6: shared validation, effective recall controls and plan/receipt-bound discovery. |
| Relation predicate parity | The v3 JSON Schema accepted any non-empty predicate while runtime allowed a closed 15-value enum. | Schema-valid input could be runtime-invalid; host/contract drift. | Fixed by making the contract enum exactly equal to the runtime set, with parity regression. |
| Query receipt lifetime | MCP held only the last 16 full audit receipts in memory with no TTL, byte budget, redaction or integrity binding. | Diagnostic metadata had incomplete lifecycle/privacy controls and provider receipt metadata was noisy. | Fixed as a three-role model described below. |
| 100k qualification fixture | First real runner attempt exceeded the production 120/min grant limit; a second layout produced 100,100 Statements because 17-character lines crossed the 12,000-character extraction chunk boundary. | The benchmark, not the product boundary, used too many governed revisions and then split synthetic Statements. | Fixed without weakening the grant/chunk limits: seeded short unique Statements, 1,000 per source section, four fragments per packet, at most 100 governed mutations. |
| 100k Living Wiki rebuild | The corrected 100,000-Statement fixture recalled every target but `rebuild_derived` failed closed with `Living Wiki file exceeds its byte bound`. | The projector rendered all 1,000 Statements of one Knowledge Revision inline, exceeding the 256 KiB Wiki page/read boundary. This prevented a rebuildable Wiki at the requested scale; raising the bound would also violate the persistent read contract. | Fixed by deterministic Statement Evidence shards: more than 64 Statements are split into registry-indexed pages of at most 64, the canonical Knowledge page links every shard, and stable Statement anchors/receipts remain registered. The clean 100k rerun completed. |
| Source-page compilation summary at 100k | The exact 100k construction run remained inside one SQLite statement for more than two hours. A minimal trace regression showed the query joined Source IR nodes and fragments before counting both. | One 200k-node table and one 100k-fragment table were joined on the same Compilation, creating an approximately 20-billion-row intermediate result. This made Wiki projection position-independent but effectively quadratic in source size. | Fixed by three independently indexed scalar aggregates. The regression forbids the dual one-to-many join and checks exact rendered counts; the clean 100k Asset construction and rebuild completed. |
| Python/scale warm integrity | `KnowledgeOS.open(...).context.compile()` performed the startup checks and then opened another store and ran full autonomous verification on every call. The construction runner hard-coded `per_request_full_verify=true`, while the Query/Graph runner opened and fully verified the 100k store once per target; four queries consumed 1,197,515 ms. | Repeated context and qualification reads bypassed the already implemented persistent read lifespan, so cost grew with canonical state and the frozen no-per-request-full-verify gate could not be measured honestly. | Fixed without a second cache: one `KnowledgeOS` handle lazily owns the existing `PersistentReadRuntime`, passes its snapshot to the canonical Capsule builder and closes explicitly. Both runners warm one verified runtime and instrument actual `AutonomousKnowledgeStore.verify` calls; unchanged warm requests must record `per_request_full_verify=false`. |

## Query v6 bounded retrieval

The new Statement path is:

```text
requested retrieval controls
→ governed recall discovery (≤20 revisions)
→ revision-bound SQL matching (≤512 Statements)
→ admission / duty selection / challenge / fallback / dedup
→ provider capsule (≤64 KiB)
```

The Query Plan and local audit bind effective controls, upstream plan hash, recall digest,
candidate/selected revision counts, selected revision digest, channels, limitations and Statement
truncation. Reaching the 512 pool emits a suppression plus a provider-visible Gap. Upstream
historical/canonical/alias/graph/reranker/contradiction bounds that report a reached bound are also
projected as discovery Gaps. No fallback changes Authority.

The scale diagnostic executes those queries through one real `PersistentReadRuntime`: startup full
verification is timed separately, every target uses the same unchanged verified snapshot, and the
report records the observed full-verify count plus `per_request_full_verify`. It does not conceal
startup cost inside warm-query latency or infer the result from source inspection.

`retrieval_mode` stays in the public v6 contract with exact values `exact`, `lexical`, `dense`,
`graph`, and `hybrid`; `graph_hops` remains 0–2. `force_canonical_lexical` is an integrity-selected
Python/runtime control and is receipt-visible. Invalid values fail at the shared service seam rather
than being silently coerced.

## Graph and Relation disposition

The development graph fixture exercises a last-inserted tail edge, hub, depth-2 chain, cycle,
active contradiction, future temporal edge, forgotten endpoint, self-loop rejection and dangling
endpoint rejection. `graph_hops=0/1/2` has distinct seed/direct/deep reachability assertions. The
runtime/Schema predicate set is exactly:

```text
alias_of, applies_to, consolidates, contradicts, contributes_to,
depends_on, derived_from, describes, implements, mentions, related_to,
reports, same_as, split_from, supports
```

The 500-admitted / 5,000-scanned relation truncation thresholds remain hard runtime bounds. A
small smoke fixture cannot exercise them and reports `not_executed`; no pass is inferred. A safe
10k/100k governed Relation constructor was not added because the public owner grant is deliberately
limited to 120 mutations/minute. Bulk Relation, truncation-position-bias and 10k/100k Relation
qualification therefore remain `not_executed` rather than weakening the production capability
boundary for a benchmark.

## Query receipt three-role model

1. Provider-visible receipt: only opaque `receipt_id`.
2. Local Query Trace: process-local, redacted and integrity-bound; at most 16 entries, 900 seconds,
   256 KiB per entry and 1 MiB aggregate audit payload, with TTL/LRU rotation and Vault-identity
   binding. Query plaintext, Source body/path, candidate scores, hidden reasoning and credentials
   are excluded. The owner deletes it by closing/restarting the owner-controlled MCP process; traces
   also clear on identity change and failed reopen.
3. Canonical mutation Ledger: unchanged and reserved for governed mutations. An ordinary query
   never appends an event.

A durable cross-process Query Trace was not smuggled into the Knowledge Ledger. That would require
an independent persistent-store contract, migration, recovery, rollback and deletion design; the
current source candidate makes the missing durability explicit.

## Focused executable evidence

The local evidence commands include:

```bash
uv run --frozen pytest -q tests/test_v013_query_graph_p0_reproductions.py
uv run --frozen pytest -q tests/test_v013_query_graph_scale.py
uv run --frozen pytest -q \
  tests/test_v013_query_trace_store.py \
  tests/test_v013_persistent_runtime.py \
  tests/test_v013_query_v6.py \
  tests/test_knowledge_mcp.py \
  tests/test_v013_runtime_retrieval_regressions.py
uv run --frozen pytest -q tests/test_v013_host_environment_isolation.py
```

The generated Query/Graph reports bind their exact runner, Schema, Query v6 and autonomy source
hashes. Scale results and environment measurements are reported in
`docs/V0_13_SCALE_RSS_QUALIFICATION_REPORT.md`; they are synthetic construction evidence only.

The final clean reports bind implementation commit
`bb6a942970186f03ea41e108a2eceaaca54e3bcb`. Their file SHA-256 values are
`ad16d230360610e40037808ad9efdd75ccd5b8b02eda7f51bec15c0a753c185a` for the
5,001/10,000 report and
`ec362bb5d57c4b702668d0a5f4098996ad8f88746f455e80a47393fb3cb6b1eb` for the 100,000
report. Every exact Statement target was selected; each target had one candidate, provider output
stayed below 64 KiB, the derived rebuild completed and warm queries recorded no per-request full
verification.

## Remaining P0/qualification boundaries

- 500/5,000 relation truncation and 10k/100k governed Relation scale: `not_executed`.
- Durable cross-process Query Trace: not implemented; process-local lifecycle is explicit.
- Real Codex, OpenCode/DeepSeek, Human Gold and exact signed 28-source Pack: `not_executed` or
  `review_pending`.
- Three OS × Python 3.11/3.12/3.13 and public artifact redownload: `not_executed`.

Therefore `release_gate_passed=false`, `competitive_claim_eligible=false`, and neither RC nor GA is
authorized.
