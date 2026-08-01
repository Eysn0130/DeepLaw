# ADR 0004: Persisted Authoritative Evidence Capabilities

- Status: Accepted for vNext implementation
- Date: 2026-08-01

## Context

Legal discovery scores previously selected immutable evidence, but the release did not separately
persist the properties that allow a segment to satisfy an Evidence Duty. A score, graph edge or
model judgement must never turn declared identity into signed official identity, unreviewed OCR
into reviewed extraction, or unknown temporal metadata into verified current law.

## Decision

`deeplaw.release/v3` and `deeplaw.sqlite/v6` persist one intrinsic
`deeplaw.evidence-capability-record/v1` row per exact segment. The release manifest binds the sorted
inventory digest. The persisted dimensions are integrity, base source identity, base Authority
metadata, extraction and provenance. Temporal capability is evaluated against the requested date;
signed-official identity is added only after the exact active release has passed catalog signature,
trust-root, sequence and activation checks. Neither state can be persisted by ranking.

Every Evidence Duty declares a capability predicate. Deterministic compilation marks a Duty
covered only when an admitted segment satisfies that predicate. Challenge Trace records the
predicate, witness, result and digest and supports replay.

Historical `deeplaw.release/v2` / `deeplaw.sqlite/v5` releases remain readable. Their intrinsic
capabilities are derived with the same deterministic rules and never receive signed-official status
from an explicit database path. `deeplaw migrate-capabilities` creates a new immutable v3/v6 release
beside the verified v2/v5 snapshot. It does not mutate evidence or the old release. Rollback
atomically reactivates that preserved release; it does not delete the migrated release.

## Consequences

- Capability state and its inventory are independently hash-verifiable.
- An installed historical release remains usable without an in-place migration.
- A migration changes release identity and receipts because the immutable release identity changes.
- The Legal Pack remains read-only to Agents; catalog trust still belongs to the owner/maintainer
  installation path.
- Semantic entailment remains `model_assessed` and cannot satisfy deterministic citation checks.
