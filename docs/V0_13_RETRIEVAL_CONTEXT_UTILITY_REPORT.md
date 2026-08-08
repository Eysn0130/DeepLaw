# DeepLaw v0.13 retrieval and context-utility disposition

Status: **local low-noise contracts/regressions passed; quality qualification remains
development-only / not executed on frozen holdouts** (2026-08-08).

## Executed local behavior

The current Query Plan v6 path is explicitly bounded:

```text
governed discovery (at most 20 Knowledge Revisions)
→ admission
→ at most 512 Statement candidates
→ duty-aware selection and contradiction/temporal challenge
→ targeted evidence fallback
→ deduplication
→ provider capsule (at most 64 KiB)
```

The provider projection contains selected Statement context, bounded evidence/citations,
Authority/verification/freshness, limitations/contradictions/gaps and an opaque `receipt_id`. It
does not contain rejected candidate bodies, candidate scores, SQL/cache/parser diagnostics, a full
graph neighborhood, local paths, credentials, full sessions or hidden reasoning. An `audit`
projection request is kept local and provider delivery is reduced to `standard`.

Default Python, both CLI Context commands and autonomous MCP Context now use this same v6
selection path through a shared domain assembler. The owner-local response is additive
`deeplaw.knowledge-capsule/v3` (maximum 262,144 bytes) and embeds the independently validated
Provider v2 projection. Explicit v5/Capsule v2 remains compatibility-only; source-free object
memory is not silently admitted into the v6 Statement surface.

Ordinary queries do not append Canonical Ledger events. The MCP runtime retains at most 16
redacted process-local Query Traces for 900 seconds, with a 256 KiB per-entry and 1 MiB aggregate
payload bound, TTL/LRU rotation, identity binding, read-time integrity checks and runtime-owner
deletion on identity change or close. It stores the query hash rather than query text and does not
store Source bodies, private paths, hidden reasoning or credentials.

The focused command was:

```bash
uv run --frozen pytest -q \
  tests/test_v013_query_v6.py \
  tests/test_v013_query_v6_context_parity.py \
  tests/test_v013_query_trace_store.py \
  tests/test_v013_runtime_retrieval_regressions.py \
  tests/test_v013_query_ablation.py
```

Result: **passed** on the current continuation. These are contract and development-fixture
regressions, not Human Gold quality evidence.

## Repository-visible development ablation

`benchmarks/v013/query-ablation-current.json` is intentionally bound to
`v013-development-query-ablation-v1`; it is not a qualification or blind holdout. Its current file
SHA-256 is `b3f06a9ca05e57c97353e5d142aaccb7574e7e32be901c6b8c3100ae596d4694`
and its internal canonical `report_sha256` is
`6d1c252937de7b9584828d9d4ddb22dd7295d6a671f138166129c7bca20bc473`.

For the eight-query synthetic denominator, expansion-on observed Recall@K `0.875`, Precision@K
`0.666667` and false-positive rate `0.333333`. Expansion-off produced the same headline values;
therefore this fixture provides no evidence that expansion improves quality. Dense and graph-only
ablations were `not_executed`; hybrid was degraded because no dense/graph channel was available.
Latency is a single local wall-clock observation and `token_proxy` is UTF-8 bytes, not model tokens.

This weak result is retained rather than promoted: it is useful for regression calibration, but it
cannot support a quality, superiority, low-noise or competitive claim.

## Frozen metrics and current disposition

The qualification protocol freezes Recall@K, Precision@K, MRR, nDCG, Useful Context Recall,
RelevantChars/ContextChars, Redundancy Rate, False Suppression Rate, Duty Coverage, Duplicate
Evidence Rate, Distractor-induced Answer Delta, Token savings, latency, RSS, storage and provider
bytes. Contradiction, exception, temporal uncertainty and Gap are protected duties and are never
classified as removable noise merely to improve compression.

| Metric group | Status | Evidence boundary |
| --- | --- | --- |
| Development Recall/Precision/false positives | `executed` | Eight public synthetic queries only; values above. |
| Provider hard limit and minimal receipt | `pass` (local regression) | Schema/output tests; not a quality score. |
| Query Trace privacy/integrity/rotation | `pass` (local regression) | In-process deterministic tests; no durable trace store exists. |
| MRR / nDCG / Useful Context Recall | `not_executed` | No repository-external frozen Gold. |
| RelevantChars/ContextChars / Redundancy / Duplicate Evidence | `not_executed` | No scored qualification capsules. |
| False Suppression / Duty Coverage | `not_executed` | No Human Gold for required contradiction/exception/temporal duties. |
| Distractor-induced Answer Delta | `not_executed` | No real-model paired answer run. |
| Token savings | `not_executed` | Development byte proxy is not model token accounting. |
| Final latency/provider bytes | `not_executed` | Local construction diagnostics are reported separately and are claim-ineligible. |

Current bindings include `contracts/provider-knowledge-capsule.v2.schema.json` at
`f5d848df17a4429f468725b49f2a9466535c9e19044e1caefa61e7369f5600f5` and
`contracts/knowledge-capsule.v3.schema.json` at
`97a5833d104cacf45f8f4d0d479027697f1cc821fc73bfcde25b1c6d7d10b7da`,
`contracts/knowledge-capsule-verification.v3.schema.json` at
`373942f9be4adc542d498f7d6f2a4d5b0da63aface33340651b8e0b014c23df1`, and
`contracts/query-audit-read.v1.schema.json` at
`c85294edf8630df26c851c1366d2036ca4f1bb5b2b78b4e7515b384bc219c60d`.

## Decision

The source candidate now has a smaller and better isolated Agent Context boundary plus executable
regression evidence. It does not have qualification-holdout or final-blind utility measurements.
Accordingly `context_quality_gate_passed=false`, `claim_eligible=false`, and
`competitive_claim_eligible=false`.
