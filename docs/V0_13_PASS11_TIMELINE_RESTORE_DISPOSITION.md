# DeepLaw v0.13 Pass 11 Run Timeline and semantic restore disposition

Status: **not_claimed**, 2026-08-11. Package remains `0.12.0`; `release_ready=false`.

## Frozen Owner tasks

Pass 11 freezes the following user outcomes before any API or schema proposal:

1. Given several completed, concurrent, resumed, forked, and compacted Runs, an owner can locate the
   attributable current decision, next action, Gap, and Artifact reference for one project/task
   line without exposing transcript, hidden reasoning, raw logs, credentials, Secret, or a local
   path. The future qualification metric is task completion plus measured time-to-locate.
2. Given an incorrect or stale Agent-derived semantic revision, an owner can select an admitted
   prior semantic state and restore its meaning by creating a new attributable Revision and Ledger
   event. The audit head must advance; restore cannot rewind or rewrite history. Wrong task,
   repository, worktree, scope, sensitivity, or stale base must fail closed.
3. Owner-directed forgetting must remove eligible private Run/Checkpoint or restored semantic bytes
   without deleting protected source evidence or corrupting audit/recovery state.

These are qualification task definitions, not proof that the tasks are currently possible or fast.
No external owner timing, independent Gold, real Host trajectory, or semantic-restore qualification
was executed in this phase.

## Existing primitives

DeepLaw already has immutable Run records, Knowledge Revisions, Ledger events, task bindings,
working checkpoints, lineage reads, owner snapshot create/verify/restore, lifecycle withdrawal, and
private-data GC primitives. Snapshot restore recovers a Vault artifact; it is not semantic restore
of one selected Knowledge Object. The existing receipt/history reads do not provide the frozen
content-minimized cross-Run owner search task as one public Timeline seam.

## Decision

- Content-minimized searchable Run Timeline: `not_claimed`.
- Semantic restore: `not_claimed`.
- New Knowledge kind, Relation predicate, database, Ledger, Run Timeline schema, public Timeline
  API, and semantic-restore API added in Pass 11 Phase 2: none.

The current public-seam insufficiency was already reproduced by
`tests/test_prd12_run_timeline_reproduction.py`, but no real Owner time-to-locate or restore evidence
exists. Adding a public surface now would choose product semantics before that evidence. The
smallest safe outcome is to preserve existing Run/Memory/Ledger/Revision primitives and keep both
claims closed until the frozen tasks are executed.

## Admission rule for future work

Only a reproduced failure on the frozen Owner tasks may admit a minimum owner-only API. Any such
change must reuse the shared domain coordinator, advance revision/event history, enforce existing
scope/sensitivity/task admission, exclude transcripts/Secrets/paths, and include contract,
migration/recovery where applicable, audit replay, rollback, security, and regression evidence.
