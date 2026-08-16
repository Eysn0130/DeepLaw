# DeepLaw v0.13 Pass 6 disposition

Status: **current source candidate remediation in progress; independent product outcome remains
prepared but not executed**.

## Candidate and authority boundary

- Branch: `codex/v013-evidence-provenance`; start commit/tree:
  `abed9d78ddfb80e63cd0ec4a7893db74d7b6191d` /
  `0df78b2d02cd78c1aeaba5fd1952eb21c20214df`.
- Package version: `0.12.0`; no version bump, tag, signature, RC, GA or registry publication.
- PR `#22` remains a Draft; final current-head commit/tree and CI are recorded in the task handoff.
- No repository `.env`, prior DeepSeek key, `~/.codex/auth.json`, Codex Desktop login state, or
  real Provider was read or used.
- No qualification wheel is frozen before independent product outcomes; `wheel_sha256` remains
  `not_executed`.

The exact reproduction, root-cause and minimum-repair inventory is in
`V0_13_PASS6_ROOT_CAUSE_LEDGER.md`.

## Query v6 expression invariance

The pre-fix public Source → Semantic Compilation → Context reproduction differed solely by
capitalization: sentence case returned the three governed `Policy Alpha` Statements, while Title
Case returned none and recorded ten `identity_anchor_mismatch` rejections. Capitalization had been
converted from an inference into eligibility.

The first Pass 6 fix removed the admission rejection but accidentally retained a second hard
`target_relevance` gate during final selection. Exact visible-semantic execution exposed the
consequence: case 08 omitted the third dated Event and case 11 omitted the revision-bound comparison
Synthesis. Removing that gate without a replacement also admitted wrong-target distractors, and the
first generic content floor regressed the single-token CJK alias query `MRC 指什么？`.

The accepted minimum repair keeps caller-explicit stable identity targets strict and keeps all
scope, sensitivity, Authority, lifecycle, temporal and evidence admission unchanged. Inferred
natural-language anchors remain bounded, digest-bound ranking hints; anchor mismatch is not an
eligibility rule. Final selection uses the existing query terms as a bounded content-relevance
floor, excludes short ASCII numeric fragments from that score, and can retain an exact lexical-hint
candidate with one meaningful term. Non-anchor candidates with sufficient content overlap remain
eligible, including the third date Event and comparison Synthesis. The 20-revision, 512-Statement
and 65,536-byte Provider bounds are unchanged. Ambiguous aliases remain multiple candidates; an
opaque unknown ID does not receive an unrelated answer.

The new public Python/CLI/MCP metamorphic suite covers lower/sentence/title case, punctuation,
quoted names, multi-target comparison, CJK + English proper names, negation/exception, alias
ambiguity, opaque unknown identities, and explicit semantic/Knowledge/Revision targets. Stable
required identities—not invocation receipts or byte order—are the invariant. This suite is
repository-visible and tuning-used development evidence. It is not Human Gold or final blind.

Query Plan v5 remains an explicit compatibility path. The final exact visible-development query
execution exercised the v5 query result and the default v6 Context together: all 15 canonical cases
and 14 variants passed, `query_variant_pass_rate=1.0`, `context_semantic_accuracy=1.0`,
`citation_validity=1.0`, `claim_evidence_binding_accuracy=1.0`,
`stale_prohibited_selections=0`, and `provider_hard_limit_violations=0`. The report SHA-256 is
`614aa331155212e9096fb492e4a5cadbee9fde6c2f0cb71543daa59287c4123d`; the cost report SHA-256 is
`c4c8c4ff566091a10ab394581442cf729ff9d822b784058d4900d7e03582c169`. These are
repository-visible, tuning-used development results, not external qualification. A suspected v5
identity-anchor relevance-floor bypass was not reproduced at a stable public seam, so Pass 6 does
not change v5 runtime semantics.

## Graph completeness

The graph view now distinguishes two independent conditions while retaining its hard bounds:

- `selection_truncated=true`: another governance-admitted Relation was actually observed after the
  requested result limit;
- `candidate_scan_truncated=true`: the 5,000-candidate scan ended with an uninspected tail.

The runtime inspects only until the first extra admitted Relation needed to establish selection
truncation, does not return it, and never scans beyond 5,000. A tail containing only rejected
Relations does not falsely report admitted truncation. Actual selected/scanned counts and at most
two bounded gaps are shared by the domain result, CLI/MCP projection and Wiki local graph. No hard
limit was raised and Relation Authority/provenance admission is unchanged.

The public Semantic Compilation attempt did not justify a private-fixture bypass. Its v3
`relation_actions` reached the finalization seam, where the frozen admitted-candidate context
exceeded the existing 64 KiB Provider bound. The attempted implementation was removed instead of
weakening that bound or writing Ledger/SQLite state directly. The resulting schema-bound local
report executed 5,001 public-semantic Statements in 26 seconds with positions `0/2500/5000`, tail
recall and position independence true, candidate bound 512, and maximum Provider bytes 7,058. Its
file SHA-256 was `3604421fc52840888b52454edd1d06e434f9aa1d5d92b4066c4048be4b4c89f1`
and its internal report digest was
`3748f7b3afe02e7bd95e86817a84f300de0e566ad33d2b9c2dc7c3de41f76773`.
Relation 500/5,000 is therefore honestly `qualification_fixture_blocked` / `not_executed`, not a
pass. Relation 10k/100k remains gated behind independent product outcomes and is not inferred from
the small public graph regressions.

## Owner outcome package

`owner_bound_external` packages now require unique mount identities and a complete explicit
mount-to-root mapping. There is no default-root fallback and no external `package_workspace`.
Compiler-only and evaluator-only domains must resolve to different roots; Gold, scorer, frozen
control, validator, raw output, Gate Result and evaluator receipt artifacts cannot be placed on a
compiler-visible mount. Corpus cannot be hidden on a Gold/scorer/output mount. Dedicated artifact
kind, purpose and visibility combinations are closed.

These checks prove only the manifest's bytes, relative references and observed root topology.
Neither JSON attestation nor an isolation receipt proves an OS sandbox. Independent execution must
still prove separate OS users or containers, read-only mounts, network policy and invisible
directories. The credential-free dry run remains benchmark-only, rejects every passed outcome,
and stops at `prepared_not_executed`.

## Platform and remaining qualification order

The historical Platform Core inventory remains immutable at 1,339 common tests with digest
`d397dc6cad207959eda7032d6cda115d992cdbbf9a58251c72c1da38a2c5537a`. The final Pass 6 pre-commit
current-source collection is 1,398 with digest
`10375738ea7171f0e7b0c4000e55740450376ecf5a860f99489dc620219ebe2e` and 59 expected drift
entries. Its candidate receipt file SHA-256 is
`ab7e20619c2a766e44f7b6dd6e0c034a4dc31f02a15103d0cd682a9a0237c683`; it explicitly keeps
`release_ready=false`. Manual Platform Core exited 1 and wrote a fail-closed receipt with SHA-256
`ee5d14c3ff874586bba2d5c205c470044b5368195598aa15ee8fa7158752ab1c`.
Candidate CI can be green without becoming a Platform Core pass. Inventory rotation remains
forbidden until independent product outcomes pass and a final candidate is frozen.

Owner must provide outside the repository:

1. an independently created qualification corpus and Human Gold whose author did not inspect
   candidate output;
2. a fresh final-blind corpus/Gold plus replacement policy after any diagnostic use;
3. the signed or equivalently verified exact Legal Pack and trust identity;
4. an owner-only isolated Codex evaluation identity/project/credential;
5. a separate evaluator/scorer workspace.

Until all five exist, the correct stopping point is `prepared_not_executed`: no fabricated Gold,
real model call, Host credential reuse, performance/scale continuation, Platform inventory
rotation, or release artifact claim. After independent continuity, Wiki and legal outcomes pass,
the remaining order is frozen performance diagnosis → 10k/100k scale and runtime lanes → exact
candidate wheel → nine OS/Python cells → SBOM/provenance/public redownload → Owner release decision.

## Local verification record

The final pre-commit local run used:

```text
uv lock --check
PYTHONDONTWRITEBYTECODE=1 uv run --frozen pytest --strict-markers -p no:cacheprovider -rs
uv run --frozen ruff check .
git diff --check

PYTHONDONTWRITEBYTECODE=1 uv run --frozen pytest --strict-markers -p no:cacheprovider -q tests/test_semantic_gold.py
PYTHONDONTWRITEBYTECODE=1 uv run --frozen pytest --strict-markers -p no:cacheprovider -q tests/test_v013_query_v6_expression_invariance.py tests/test_v013_query_v6_multilingual_context.py tests/test_v013_query_v6_unseen_development.py tests/test_v013_query_v6_context_parity.py
PYTHONDONTWRITEBYTECODE=1 uv run --frozen python -m benchmarks.v013.query_graph_scale --output REPORT.json --scale 5001
uv run --frozen python -m benchmarks.v013.product_outcome_package --dry-run
uv run --frozen python -m benchmarks.release.platform_inventory --mode platform_core --selection common --require-match --output RECEIPT.json
```

The complete suite returned `1400 passed, 6 skipped in 386.17s`. The skips are not release passes:
historical v0.6 wheel unavailable; Statement 10k and 100k not executed; Relation 500/5,000
qualification fixture blocked as described above; native Windows ACL and junction require Windows.
`uv lock --check`, Ruff and `git diff --check` passed. Current-head GitHub Candidate CI remains a
post-commit requirement and is reported in the handoff rather than self-referenced here.

## Gate status

| Status | Value |
|---|---|
| `source_candidate_stable` | `true` locally; current-head Candidate CI pending |
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

Known limitations are the absent external outcomes/Host/Legal inputs, the retained frozen Living
Wiki performance failure, Relation 10k/100k and other post-outcome scale/RSS/concurrency/cache
lanes, unrotated Platform Core inventory, missing historical v0.6 compatibility wheel, and absent
SBOM/provenance/public-redownload evidence.

Final release disposition: `source_candidate_remains_not_released`.
