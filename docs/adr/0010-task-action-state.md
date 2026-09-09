# ADR 0010: Host-reported action state in the existing Run Ledger

Status: Implemented candidate; finite synthetic tests, not native Host qualification.

## Decision

An external action and successful checkpoint recording are different events. A checkpoint Run
continues to describe recording the checkpoint. A separate immutable Run may carry
`task-action-state/v1` under its exact `task_binding`. No action executor, new canonical database,
or provider permission is introduced.

The action state is closed: opaque `action_id`, request hash, outcome hash or null, exact expected
predecessor Run, and `not_executed`, `initiated_unknown`, `succeeded`, or `failed`. Evidence remains
`host_reported` and never creates independent verification or Authority. Only the sequence
`not_executed -> initiated_unknown -> succeeded|failed` is admitted. The action identity and request
hash cannot change. Terminal states cannot transition. A different action needs a fresh identity.

`record_run` commits each transition under the existing Sink grant, rate limit, idempotency record,
Run transaction and audit event. It compares the expected predecessor while holding `BEGIN
IMMEDIATE`; predecessor order comes from Ledger sequence, not timestamps that may share a second.
The action's route, scope and sensitivity stay fixed. A legitimately granted new Host writer may
record the next observation on that same route. The Run input/output hashes must equal the action
request/outcome hashes. Pending action Runs are `partial`, and are excluded from checkpoint-route
identity discovery.

## Host protocol and bounds

The Host records `not_executed`, then durably records `initiated_unknown` **before** attempting the
external action. If either receipt is unavailable, the Host does not start the action. After a lost
response or restart, `verify_external_state` means inspect the target system or its retained receipt
before recording an outcome. DeepLaw never invokes the action, retries it, or supplies external
exactly-once guarantees. A crash after unknown registration but before execution is conservatively
unknown too. An outcome hash binds the Host's report; it does not prove the report true.

Owner CLI `deeplaw knowledge task record-action` exposes this protocol with explicit grant, task
handle, workspace, idempotency key, action identity, request hash, status, optional outcome hash and
expected prior Run. The same state is available through Sink input v7 / output v5. Ordinary Run
records retain v1 bytes; action Runs use `knowledge-run-record/v2`.

Resume/compaction return at most 16 current action states in `task-continuity-result/v3`, with
`not_started`, `verify_external_state`, `do_not_repeat`, or `review_failure` data requirements.
Overflow is a Gap. Query/context withhold a working checkpoint while an admitted action is unknown,
or when its action state changed after the checkpoint Run. A fresh checkpoint records bounded
Host-reported action summaries; it does not reinterpret them as verified external facts.

The thin native Hook accepts the explicit `host-continuity-capsule/v2` action projection: at most
four opaque action identities/states/requirements, no internal Run IDs or hashes. Existing v1
capsules without actions remain supported. The original 1400-byte Host capsule limit remains in
force; overflow yields a Gap rather than discarding a safety requirement. The native lifecycle
receipt uses v2 to bind task continuity result v3. These protocol tests do not establish any real
Codex/OpenCode qualification slot.

## Persistence, recovery and rollback

Canonical action state is additive versioned JSON inside `knowledge_run_records_v4.metadata_json`.
Existing rows and receipt digests are unchanged. Two rebuildable expression indexes accelerate
exact action and route lookup; initialization creates them without rewriting Run rows. Integrity
verification checks receipt hashes, audit binding and the bounded predecessor chain. Snapshot
restore and checkpoint-index rebuild preserve failed/unknown states without executing anything.

A binary that understands only Run v1 must fail closed on action Run v2. Do not strip metadata or
rewrite history to make rollback appear compatible. Restoring an older snapshot cannot reveal
external actions performed after that snapshot: preserve their receipts and reconcile external
state with the owner before continuing. Knowledge rollback does not undo or authorize repeating
an external side effect.

## Evidence ceiling

`test_task_action_state.py`, `test_task_action_ledger.py` and the native Hook regression cover the
finite state contract, predecessor conflicts, route isolation, CLI recovery, idempotent terminal
receipt replay, failed/unknown snapshot recovery, bounded projection and one synthetic external
invocation across response loss. No model, real external service or real native Host is executed by
these tests. Actual external truth remains the Host/target-system verification responsibility.

The owner-side lifecycle adapter emits `native-host-lifecycle-receipt/v4`, binding task
continuity result v3. The existing lifecycle receipt v1 and provenance-labelled native
observation receipts v2 and v3 retain their original schemas; v4 does not replace or reinterpret
those persisted formats. Host-reported action Runs cannot establish `run_bound`
verification or back a working checkpoint. The checkpoint requires a separate ordinary
successful task-bound Run.
