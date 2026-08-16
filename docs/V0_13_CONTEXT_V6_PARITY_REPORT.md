# DeepLaw v0.13 Context Query Plan v6 parity remediation

Status: **source candidate only**. This report is evidence for the working tree after the Context
default-drift remediation; it is not a release, RC, GA, superiority, or completeness statement.
The package version remains `0.12.0`.

Core Scope Freeze addendum: a later public-seam reproduction showed that a current, TTL-bound,
run-bound working Task Checkpoint was absent from Statement Evidence and therefore returned
`no_answer`. Default v6 now admits only a closed structured checkpoint projection as a
non-authoritative interpretation. Generic source-free memory, unbound working memory, raw-log
text, historical/expired revisions, legal purposes, credentials, and local absolute paths remain
inadmissible. See `V0_13_CROSS_THREAD_CONTINUITY_REPORT.md`. The earlier sentence below about
source-free memory remains true for unbound or unstructured memory and must not be read as a ban on
the narrow run-bound checkpoint seam.

## Finding and scope

The evidence baseline `ddfcc36669236716700e49816fb29b05532020e9` documented v6 defaults for
CLI/MCP/Python, but dynamic public-seam reproduction found that autonomous Context still called
the object-level v5 assembler (`knowledge-capsule/v2`). The failure was not fixed by changing a
version string: v5 and v6 have different selection, Statement, evidence, receipt, and Provider
projection contracts.

The remediation is limited to autonomous Context. `deeplaw recall` remains the legacy
`retrieval_fabric` path. The v6 Context path now covers:

| Entry point | Default | Explicit compatibility |
| --- | --- | --- |
| Python `KnowledgeOS.context.compile` | Query Plan v6, local Capsule v3 | `query_plan_version="5"` → local Capsule v2 |
| `deeplaw knowledge context` | Query Plan v6, local Capsule v3 | `--query-plan-version 5` → local Capsule v2 |
| `deeplaw knowledge autonomy context` | Query Plan v6, local Capsule v3 | `--query-plan-version 5` → local Capsule v2 |
| Autonomous MCP `operation=context` | Query Plan v6, Provider Capsule v2 | Explicit v5 output/v3 with Capsule v2 and Query Plan v5 semantics |

## v6 response and boundary contract

One shared domain assembler is used by Python, CLI, and MCP. Its local
`deeplaw.knowledge-capsule/v3` response retains the complete
`deeplaw.knowledge-query-plan/v6` plan and hash, selected
Statements, evidence, contradictions, gaps, receipt identity, budgets, audit head, sealed
capsule identity/digest, and `write_performed=false`. The local payload is capped at 262,144 bytes.

The nested `deeplaw.provider-knowledge-capsule/v2` projection is separately capped at 65,536
bytes. Provider-visible data is limited to bounded Statement/evidence context, authority,
verification, freshness, limitation/contradiction/gap state, delivery metadata, and opaque
`receipt_id`. The Provider never receives the full Query Plan, candidate scores, rejected-candidate
text, SQL/cache/parser diagnostics, local paths, credentials, or hidden reasoning. An `audit`
request is reduced to the Provider `standard` projection.

`query_target`, `applicable_duties`, `projection`, `graph_hops`, `retrieval_mode`, and
integrity-selected canonical lexical fallback are explicit v6 plan controls. Invalid controls fail
closed; they are not silently accepted and dropped. Ordinary query/Context operations do not write
the Canonical Ledger. The local Query Trace is bounded, redacted, non-persistent,
TTL/LRU-managed, integrity-checked, and runtime-owner deletable.

## Candidate validation

The implementation freeze for this continuation is
`ee06bb3ef9989c671638deda95968690d628f8ca` (tree
`5fe2895a7c50f496a23612969844cd390b3cafad`). The following are focused source-candidate
results, not final release gates:

```text
uv run --frozen pytest -q tests/test_v013_query_v6_context_parity.py
10 passed

uv run --frozen pytest -q tests/test_context_compiler.py
23 passed

uv run --frozen pytest -q tests/test_source_compilation.py
passed

uv run --frozen pytest -q \
  tests/test_v013_query_v6_context_parity.py \
  tests/test_context_compiler.py \
  tests/test_source_compilation.py \
  tests/test_v013_query_v6.py \
  tests/test_v013_query_trace_store.py \
  tests/test_v013_runtime_retrieval_regressions.py \
  tests/test_knowledge_sink_mcp.py
195 passed

uv run --frozen ruff check <Context remediation files>
passed

git diff --check -- <Context remediation files>
passed
```

The initial post-fix run exposed three pre-existing cache/invalidation regressions that were still
using default Context while asserting source-free object bodies. They were corrected to request
explicit v5 compatibility because their subject is cache invalidation, not v6 Statement admission.
No implicit v5 fallback was added. A separate Profile-v3 fixture proves that default Python and MCP
Context select the same real Statement/evidence projection, while source-free memory produces an
explicit v6 `no_answer` Gap.

The v3 verifier independently checks the closed envelope, plan hash/schema, deterministic receipt
derivation, task/goal query identity, selected Statement/evidence/Gap identities, Provider/local
projection consistency, budget identity, 64 KiB Provider content limit, 256 KiB local limit,
Capsule digest/ID, Vault identity and audit anchor. Resealed plan, receipt, projection and budget
tampering regressions fail closed.

Post-freeze synthetic Statement qualification used one clean worktree at the implementation
commit. Exact targets at 5,001, 10,000 and 100,000 Statements were all selected at the beginning,
middle, position 5,001 where applicable, and tail; every target had one candidate and Provider
content remained at or below 6,037 bytes. The report file SHA-256 values are
`70f5d551a4bdcc9cbcf1a2210652577068afa9bf8168eae40b002757b2c3e424` and
`e69d2f6eb7115db45a56137d224a2320b3f7633b06cae86185fe9248fa3bca5f`.
These lanes remain synthetic and claim-ineligible. Their Relation/truncation sections correctly
remain `not_executed`.

## Not executed and disposition

Real Codex/OpenCode/DeepSeek hosts, Provider calls, owner evaluation credentials, Human Gold,
legal Pack, 10k/100k governed Relations, 500/5,000 Relation truncation, a post-remediation
10,000-request RSS rerun, cross-platform fresh wheels, SBOM/provenance, public re-download, and
final release artifacts were not executed by this continuation. The prior baseline v6
qualification conclusion is superseded for Context and must not be reused as post-remediation
evidence.

`release_gate_passed=false`

`claim_eligible=false`

`competitive_claim_eligible=false`
