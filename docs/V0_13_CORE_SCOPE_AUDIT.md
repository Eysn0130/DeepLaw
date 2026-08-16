# DeepLaw v0.13 Core Scope Freeze audit

Status: **source candidate / scope frozen / not released**. The audit baseline is
`faa72dd6cfd92ace712e55da7a5c7c875400d965`; package version remains `0.12.0`.
This audit records current behavior. It does not authorize deletion, a version change, an RC, a
GA, or a quality claim.

The reviewed implementation freeze is commit
`450e79e66a30399385ab4afd2d137414e78b7119`, tree
`c368e2ccbb45ae1e641e29d74e458b587c6fe6ba`.

## Product boundary

The v0.13 continuation is reduced to two product outcomes:

1. a bounded Task Checkpoint can restore cross-thread task state through one read-only Context
   entry point; and
2. a human and an Agent can traverse the same Evidence Wiki chain back to exact Source identity
   and evidence without changing Source bytes or Authority.

Guides, Codemap, Relation Path, Obsidian Bases, new page families, new predicates, new kinds, new
databases, new Host adapters, complete `as_of` Wiki browsing, and single-Revision revert are outside
this freeze. No current public Schema was expanded.

## Public retrieval surface matrix

| Surface | Actual default and output | Consumer role | Disposition |
| --- | --- | --- | --- |
| `deeplaw recall` | Legacy `retrieval_fabric` v1; not Query Plan v6 | Existing general CLI compatibility | **DEFER** removal; inventory callers first |
| `deeplaw knowledge recall` | The same legacy `retrieval_fabric` v1 path | Compatibility alias in the Knowledge CLI | **DEFER** removal; do not recommend |
| `deeplaw knowledge autonomy recall` | Autonomous recall v1; no Query Plan v6 Capsule | Internal discovery used by v6 plus operator diagnostics | **SIMPLIFY** documentation and keep as an internal/operator seam |
| Python/CLI/MCP `knowledge query` | Query Plan v6 by default; purpose-aware local diagnostic output | Operator inspection of plans, controls, selection, gaps, and receipts | **KEEP** as the operator debugging surface |
| Python/CLI/MCP `knowledge context` | Query Plan v6, local Capsule v3, bounded Provider v2 projection | Agent task admission and delivery | **KEEP** as the only recommended Agent entry |
| `knowledge autonomy context` | Shared v6 Context assembler; explicit v5 compatibility remains | Compatibility/operator alias | **SIMPLIFY**; stop presenting it as a second Agent product |
| Explicit v5 / Capsule v2 | Only when the caller explicitly requests v5 | Migration and regression compatibility | **DEPRECATE** for new consumers, but do not remove this round |
| MCP `operation=recall` | Existing deprecated MCP compatibility route | Old MCP consumers | **DEPRECATE**; replacement is `query`, while Agent delivery uses `context` |

The matrix was checked across `KnowledgeOS`, both Knowledge CLI groups, autonomous MCP, the golden
CLI, and retrieval-fabric callers. The version and Context parity regressions remain the executable
authority; documentation does not override the runtime.

## Duplication and deprecation proposal

The recommended product shape is deliberately small:

```text
Agent -> deeplaw knowledge context
Operator -> deeplaw knowledge query
v6 discovery internals -> autonomous recall
Explicit compatibility only -> v5 / Capsule v2 and legacy recall aliases
```

No legacy command is deleted in v0.13. Before a later removal, the owner must identify scripts,
plugins, adapters, documentation examples, and external MCP clients that still consume the exact
legacy output shape; publish a compatibility interval; and add an explicit runtime warning where
one does not already exist. `deeplaw recall` and `deeplaw knowledge recall` must be assessed
together because they currently represent one implementation, not two products.

## Core continuity defect and allowed remediation

The read-only audit reproduced one release-blocking core defect: an owner-granted, TTL-bound,
run-bound `memory_type=working` checkpoint was current in the Ledger but absent from Statement
Evidence, so default v6 Context returned `no_answer` even for an exact `knowledge_id` target.

The minimal remediation does not add a kind, predicate, Schema, table, or write path. v6 may create
an ephemeral, non-authoritative `interpretation` projection only when the current Revision is:

- active, unexpired, admitted for scope and sensitivity, and bound to an immutable successful Run;
- `memory_type=working`, tagged `checkpoint`, and addressed by a checkpoint semantic key;
- a closed, bounded Task Checkpoint containing only `GOAL`, `CONFIRMED_DECISION`, `CONSTRAINT`,
  `VERIFIED_FACT`, `OPEN_GAP`, `NEXT_ACTION`, and `ARTIFACT_REF` records; and
- free of credential assignments and local absolute paths.

Generic working memory, source-free/unbound memory, historical revisions, raw tool-log text,
legal/quote/verify/historical purposes, expired items, and mismatched targets remain inadmissible.
The projection has `origin=agent_derived`, `authority=agent_derived`,
`legal_authority=false`, no Source references, and an explicit limitation. Query and Context remain
read-only and preserve the 64 KiB Provider bound.

## Audit decision

The surface should be **contracted in documentation and future migration, not physically deleted
in this candidate**. Continue only the Context continuity and Evidence Wiki qualification lanes.
Do not add adjacent Wiki, graph, Host, or UI features until those two outcomes have repository-
external Human Gold and real-host evidence.

`release_gate_passed=false`

`claim_eligible=false`

`competitive_claim_eligible=false`
