# ADR 0006: Shared kernel and three product roles

- Status: Accepted architectural freeze
- Date: 2026-08-17

## Context

DeepLaw serves three related jobs: preserving task-lineage state across Host threads, preserving
professional material as exact source-native evidence, and giving humans and Agents a readable
Living Wiki. Treating those jobs as separate products would duplicate identity, revision,
Authority, retrieval, and lifecycle rules. Treating the Wiki as a canonical copy would also lose the
source format, version, locator, and exact-byte obligations of professional evidence.

The existing governed kernel already provides immutable Source Revisions, Document/Version/Fragment/
Locator identity, CAS and Registry, Knowledge Revisions, Ledger and lineage, Authority, scope,
sensitivity, bitemporal lifecycle, grants, contradiction and Gap state, receipts, backup, forget,
and recovery. The architecture needs a stable product boundary that uses those primitives without
adding another store, Knowledge kind, Relation predicate, page family, Host runtime, or retrieval
engine.

## Decision

Freeze DeepLaw as exactly three product roles on one shared governance kernel:

1. **Task Continuity / Governed Project Knowledge** exposes bounded, attributable task state and
   explicit lineage recovery without storing or replaying a Host transcript.
2. **Source-native Evidence Library** preserves exact source bytes and their stable Document,
   Version, Fragment, and Locator identity. Legal Pack is its first-party legal policy plane and
   remains an isolated read surface where required.
3. **Living Wiki** projects governed identities, relations, evidence links, freshness, limitations,
   and Gaps for human/Agent co-reading. It is not an editable canonical copy of professional
   sources.

The three roles use one Context Compiler with the fixed conceptual flow
`Discovery -> Admission -> Selection -> bounded Knowledge Capsule -> thin Host drivers`. The
Context Compiler is not a fourth product and is not a second retrieval engine. Automatic transcript
memory and background transcript scraping are prohibited.

`ARCHITECTURE.md` remains the sole current architecture specification. This ADR records the
boundary decision and its rationale; it does not add a public contract, persistent schema, release
status, or qualification claim.

## Rejected alternatives

- **Separate knowledge engines per role or policy plane:** rejected because identity, provenance,
  Authority, lifecycle, and retrieval would drift and cross-plane evidence could be misrepresented.
- **Wiki as the canonical professional document:** rejected because Markdown projections cannot
  replace exact source bytes, source format, version, locator, temporal chain, or legal evidence.
- **A fourth Context or Agent-memory product:** rejected because bounded context selection is a
  shared kernel service and automatic transcript memory violates the privacy and governance boundary.
- **A new database or Host-specific runtime to join the roles:** rejected because the existing CAS,
  Registry, Ledger, Coordinator, and thin adapters provide the required seams.

## Consequences

- Product documentation, public journeys, and qualification tasks must identify one of the three
  roles while reusing the same kernel identity and governance semantics.
- Source-native Evidence remains source-first for exact quote, version, effective-date, exception,
  cross-reference, and completeness duties; Wiki pages remain bounded projections.
- Host adapters remain thin and cannot create retrieval, persistence, transcript, or mutation logic.
- Any future architectural change must show a real failing task, a mapped PRD outcome, an ADR,
  migration/recovery and security impact, and explicit Owner approval before introducing a new
  primitive.
