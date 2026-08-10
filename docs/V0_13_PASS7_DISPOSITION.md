# DeepLaw v0.13 Pass 7 disposition

Status: **current source candidate correctness repaired; external product outcome prepared but not
executed; not released**.

## Candidate and authority boundary

- Branch: `codex/v013-evidence-provenance`; start commit/tree:
  `09e1c95991329b9dcae41ba1d4dc2d0e215b55ac` /
  `dd09017f9ba688592f041bb30914bab4effc7ec2`.
- Package version: `0.12.0`; no version bump, tag, signature, RC, GA or registry publication.
- PR `#22` remains Draft. Final implementation/docs commit, tree and current-head CI belong in the
  handoff because this document cannot hash its own future Git commit.
- No repository `.env`, prior DeepSeek key, `~/.codex/auth.json`, Codex Desktop login state or real
  Provider was read or used.
- No qualification wheel was frozen; `wheel_sha256=not_executed`.

The exact fail-before evidence, root causes, risks and minimum repairs are in
`V0_13_PASS7_ROOT_CAUSE_LEDGER.md`.

## Query v6 Context outcome integrity

The accepted fix removes the remaining capitalization-dependent eligibility switch. Inferred
natural-language anchors are bounded ranking hints only. The independent relevance floor is
case-folded and may use governed title/alias lexical terms, while caller-explicit stable identity
targets remain strict. Scope, sensitivity, Authority, lifecycle, temporal, evidence, 20-revision,
512-Statement and 65,536-byte Provider boundaries are unchanged.

The public metamorphic development suite checks stable semantic/Knowledge/Revision identity sets,
not byte-identical invocation artifacts. It explicitly labels four Policy Alpha identities as
required and two alias-collision identities as optional, and covers case, punctuation, quotes,
multilingual text, exception/contradiction duties, multi-target comparison, ambiguous aliases,
opaque unknown identity and explicit IDs through Python, CLI and MCP. This suite was used for
remediation and is therefore `tuning_used_development`, not independent Gold.

## Context measurement boundary

The v0.13 development workflow now emits three distinct records:

1. `deeplaw.semantic-query-run/v1`: historical mixed compatibility report whose retrieval command
   is Query Plan v5; it remains a non-qualifying operator diagnostic rather than a pure v5 outcome;
2. `deeplaw.semantic-context-outcome/v2`: primary Query Plan v6 Context outcome;
3. `deeplaw.semantic-query-cost/v2`: additive Context delivery cost record.

The v2 Context record separates owner-local Capsule v3, Provider Capsule v2, full MCP tool-result,
selected Provider content and transport metadata bytes. The MCP tool-result is the basis of the
development token estimate because it is the complete Agent-facing result. UTF-8 bytes are not
called tokens; `actual_provider_input_tokens` remains null without tokenizer/Provider usage.
`token_savings` and `distractor_induced_answer_delta` remain `not_executed` until equal-task,
equal-duty, equal-budget baselines are frozen. Both v2 records are closed, development-only and
`qualification_eligible=false`; they do not enable commercial assembly.

The final Pass 7 local development execution passed 15/15 canonical cases and 14/14 variants for
the v5 diagnostic and default v6 Context. The v1 diagnostic report SHA-256 is
`e7a172010c3ab38d0b5dfe7f57433fe52639750922bb1d39460c4b1d68777722`; after using the full v5
payload, its historical `bytes_saved_ratio` is correctly negative (`-3.733177`) rather than a
content-only saving. The Context v2 report SHA-256 is
`679e306251fef04b9c0dde884ef51dddd37a1e5c9ddeb384174a4519e57a099d`; the cost v2 SHA-256 is
`41e529e8e68f4b3a7382ac0ffbcf15649366aa6470a613b75f933bb075c56bcf`.

The v6 development metrics are: Useful Context Recall `1.0`, False Suppression `0.0`, Duty
Coverage `1.0`, RelevantChars / ContextChars `3094 / 10106` (`0.306155`), redundancy `0.044524`,
duplicate-evidence rate `0.358319`, Provider Capsule bytes `97573` total, complete MCP tool-result
bytes `101938` total, selected Provider content bytes `93025` total, transport metadata `8913`
bytes (`0.087436`), estimated input tokens `25491`, Context latency p50/p95 `1148/1296 ms`, MCP
latency p50/p95 `662/822 ms`, and zero Provider hard-limit violations. The byte totals aggregate
15 individually bounded requests; none is a single payload. Distractor delta and token savings are
`not_executed`. These measurements remain tuning-used and cannot satisfy Human Gold or a
competitive claim.

## Graph and outcome mount integrity

Graph selection and candidate-scan truncation are now independent. Stopping after the first extra
admitted Relation proves only `selection_truncated`; it does not claim the candidate scan bound was
reached. A candidate-scan Gap is emitted only after the real bound limits inspection, and records
the active bound. The production 500-admitted / 5,000-scanned limits are unchanged. Relation
500/5,000/10k/100k remains required and `not_executed`.

Owner package root validation now compares capability-bearing visibility domains after canonical
path resolution. Compiler-visible roots (`compiler_only`, `compiler_evaluator`) cannot equal or
nest with protected roots (`evaluator_only`, `owner_evaluator`) in either direction. Protected
evaluator artifacts may share their evaluator workspace where purpose/kind rules allow it. This
closes manifest topology, not OS isolation: separate users/containers, read-only mounts, directory
invisibility and network policy still require external execution evidence.

## Qualification stopping point

Owner must provide outside the repository:

1. independently authored continuity qualification corpus + Human Gold;
2. fresh final-blind corpus/Gold and replacement policy after diagnostic use;
3. independent Wiki human-task Gold;
4. signed or equivalently verified exact Legal Pack + legal Gold;
5. separated compiler/evaluator OS users or containers with read-only mounts;
6. owner-only isolated Codex evaluation project/identity/credential.

At least one required input is absent, so the correct outcome is `prepared_not_executed`. No model
output may substitute for Gold. Scale/performance continuation, Platform Core inventory rotation,
final wheel, SBOM/provenance and public redownload remain after independent continuity/Wiki/legal
outcomes and are not inferred from Candidate CI.

## Gate status

| Status | Value |
|---|---|
| `source_candidate_stable` | `true` locally after final regression; current-head CI required |
| `product_outcome_qualified` | `false` |
| `human_gold_qualified` | `not_executed` |
| `wiki_human_task_qualified` | `not_executed` |
| `legal_pack_qualified` | `not_executed` |
| `real_codex_qualified` | `not_executed` |
| `cross_host_qualified` | `not_executed` |
| `performance_qualified` | `false` |
| `scale_qualified` | `false` |
| `platform_core_qualified` | `not_executed` |
| `artifact_redownload_qualified` | `not_executed` |
| `release_ready` | `false` |
| `competitive_claim_eligible` | `false` |

Known limitations are the absent Owner inputs, retained Living Wiki performance blocker, required
Relation/Statement/Wiki scale and RSS/concurrency/cache lanes, unrotated Platform inventory,
missing historical v0.6 wheel, and absent release artifact/public-redownload evidence.

## Local verification record

The final staged-worktree verification used:

```text
uv lock --check
PYTHONDONTWRITEBYTECODE=1 uv run --frozen pytest --strict-markers -p no:cacheprovider -q \
  tests/test_v013_query_v6_expression_invariance.py \
  tests/test_v013_query_v6_multilingual_context.py \
  tests/test_v013_query_v6_context_parity.py \
  tests/test_v013_graph_completeness.py \
  tests/test_v013_product_outcome_package.py \
  tests/test_semantic_gold.py
PYTHONDONTWRITEBYTECODE=1 uv run --frozen pytest --strict-markers -p no:cacheprovider -rs
uv run --frozen ruff check .
git diff --check
```

The complete suite returned `1410 passed, 6 skipped in 423.22s`. The skips are non-results:
historical v0.6 wheel unavailable; Statement 10k and 100k not executed; Relation 500/5,000 bulk
qualification not executed; native Windows ACL and junction require Windows. No skip is converted
to a pass or release evidence. The current common Platform collection is 1,407 cases with digest
`8782c624f2fc19f87ead9b51dfa96f23786722588090af039ca61b6b6d642b35`, 68 entries beyond the
immutable 1,339-case inventory and no missing entry. Manual Platform Core correctly exited nonzero
on the dirty, drifted candidate; the inventory was not rotated. Current-head GitHub Candidate CI and Semantic workflow remain
post-commit checks and are recorded in the handoff, not self-asserted here.

Final release disposition: `source_candidate_remains_not_released`.
