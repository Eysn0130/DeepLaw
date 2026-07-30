# ADR 0002: Keep Authority and operational status as independent dimensions

Status: Accepted
Date: 2026-07-30

## Context

Source origin, verification, legal Authority, lifecycle, epistemic state, scope, sensitivity,
freshness and compilation status answer different questions. Collapsing them into one confidence or
ranking score would allow discovery signals or successful compilation to create permissions and
Authority that they do not possess.

## Decision

Represent and enforce these dimensions independently:

| Dimension | Examples | What may change it |
| --- | --- | --- |
| origin | official, user source, agent derived, external import | the governed creation path |
| verification/Authority | signed source, source-bound, agent derived | explicit source/trust policy; never ranking |
| legal Authority | true only for protected authoritative evidence | maintainer/owner legal-pack workflow |
| lifecycle | active, superseded, archived, revoked, quarantined, forgotten | governed lifecycle mutation |
| epistemic state | supported, tentative, contested | evidence-bound revision |
| freshness | fresh, stale, invalidated, unknown | dependency evaluation |
| scope/sensitivity | personal/project/domain; public/internal/private | owner-created grant and source policy |
| valid/transaction time | bitemporal intervals | immutable revision/event |
| compilation status | planned through succeeded/failed/aborted | Compilation Run saga |

Agent-compiled and backfilled knowledge is always `agent_derived` and
`legal_authority=false`. `succeeded` means the governed compilation transaction and projection
completed; it does not mean the content is official, human-verified, legally applicable or
executable. `fresh` means registered dependencies are current; it does not prove truth.

Discovery scores, embeddings, reranker confidence, graph weight, link count, usage and Agent votes
may rank candidates only. Admission evaluates Authority, lifecycle, scope, sensitivity, temporal
intent and freshness before provider-visible selection.

## Consequences

- A stale official source remains official history but is not silently presented as current.
- A fresh Agent synthesis remains Agent-derived and non-authoritative.
- Human verification of an interpretation does not turn it into source or legal Authority.
- Editor frontmatter, Wikilinks and Canvas cannot grant identity or capability.
- Query receipts can explain rejection without conflating relevance with permission.

## Rejected alternatives

- **One trust/confidence score:** cannot safely encode permission, provenance and time.
- **Authority from majority vote or model confidence:** lets discovery signals elevate governance.
- **Compilation success as verification:** validates a transaction, not the truth of its claims.
- **Wiki or editor state as Authority:** bypasses the trusted Ledger and immutable evidence.
