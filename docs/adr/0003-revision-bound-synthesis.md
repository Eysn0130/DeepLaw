# ADR 0003: Revision-bound Synthesis provenance

- Status: Accepted for vNext implementation
- Date: 2026-08-01

## Context

A compiled Synthesis is derived from an exact set of Source, Knowledge, Relation and Compilation
Run revisions. Labelling it `source_bound` implies that one set of direct Source references is the
complete provenance boundary; labelling it `run_bound` omits the revision dependency set. Both are
semantically incorrect for cross-revision Synthesis.

## Decision

`deeplaw.knowledge-object/v3` adds `verification=revision_bound`, permitted only for Synthesis.
Every new compiled Synthesis must bind a canonical `synthesis_input_sets_v1` record and its digest,
plus the corresponding source and revision dependency rows. Admission checks the complete current
dependency set. Source withdrawal or successor propagation makes the old Synthesis stale; it is not
silently treated as current.

The SQLite change is additive in value semantics: existing columns are textual and require no table
rewrite. Existing v1/v2 Markdown and their historical `source_bound` values remain readable and
verifiable. New writes use v3. Rollback to v0.11 code remains possible from a pre-upgrade snapshot;
older code must not be used to write a Vault after it contains v3 Markdown.

## Consequences

- Query Plan v5 can report `revision_bound_synthesis` from the canonical verification value.
- Integrity verification fails if a revision-bound Synthesis lacks its exact input-set record.
- Ranking, model confidence and editor metadata cannot create this verification status.
- This status remains Agent-derived and `legal_authority=false`.
