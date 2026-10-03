# ADR 0009: complete support sets

Status: implementation candidate with bounded contract/successor/snapshot regressions;
not qualification evidence.

Flat evidence arrays bind exact inputs but cannot distinguish `(A AND B) OR C`
from `A AND B AND C`. Treating either interpretation as implicit would change the
meaning of previously committed evidence. Statement v2 and semantic publication
plan v4 therefore introduce bounded `support_sets`: alternatives whose members
must all remain admissible. Each set names exact Source/fragment, Knowledge and
Relation revisions using the existing reference types. The flat reference fields
remain the exact union inventory; they are not the support expression. Empty member groups, duplicate alternatives, missing union members and unbounded expressions
are rejected. Unsupported statements may explicitly carry zero alternatives; a supported
statement must carry at least one complete set. No model confidence or navigation link can create support.

The immutable Statement, evidence map and receipt bind the expression into their
input digest. Existing CAS artifacts and Ledger commit remain canonical; reverse
dependency rows are shared, rebuildable indexes. Migration preserves every v1
artifact byte and its all-input semantics. New v2 rows require the explicit
support-set contract. Rollback cannot relabel or discard them: an older runtime
must reject unsupported contracts, or the owner must restore a verified snapshot
from before the upgrade. Recovery and integrity verification must replay the
same version and expression, including failed commits and duplicate requests.

Admission evaluates a complete alternative against current identity, scope,
sensitivity, lifecycle, time and exact provenance. A branch containing an unknown
or unavailable dependency is not supported. A cycle cannot establish its own
support; an independently grounded alternative may still succeed. Evaluation is
bounded and returns unknown on exhaustion. Plain navigational cycles remain valid.
Historical intent continues to require the existing exact temporal policy.

Source changes and owner-directed withdrawal must re-evaluate the affected
consumers through the shared Coordinator. Independent support preserves the
consumer's canonical revision; loss of support denies future query, Wiki, read
and Capsule verification. No query performs hidden repair, and invalidating a
Capsule does not erase a remote Host copy or undo an external action.

The implementation stores v2 expressions in the existing immutable JSON/CAS
artifact columns; no SQL table rewrite is required. Version-dispatched validators
preserve v1 digests and reject unsupported versions. The public v4 stage/commit
and snapshot/restore tests verify this additive migration strategy. Statement
Knowledge/Relation references now enter the existing reverse-dependency table.
The Source refresh transaction rechecks both input audit heads. Complete support
is evaluated using prospective Source states during propagation, and an unchanged
complete alternative avoids unnecessary synthesis recompilation.

Exact successor admission cannot use an arbitrarily ordered fragment event as the
state of another fragment. It requires that fragment's dependency state and a
matching exact quote in the active successor. Local v7 delivery selects only the
surviving witness and binds `support_set_sha256`; explicit v6 withholds v2
Statements instead of silently changing the old projection contract. Historical
v2 selection currently remains withheld; immutable artifacts remain inspectable.
The public parent-dependency withdrawal regression covers joint-support denial,
independent-support retention, unchanged canonical identity, old Capsule rejection,
derived rebuild and post-withdrawal snapshot restore. Derived input verification
resolves Knowledge/Relation revision identity at the recorded manifest time;
historical source witnesses without an exact admitted binding remain unknown.
The wider transitive/fan-out matrix remains under verification.

### Direct relations and retained snapshots

Direct Sink relations register source-fragment and exact endpoint-revision dependencies in
the existing shared dependency tables, atomically with the relation. Their direct source
dependencies have a null compilation Run; only direct relation rows permit this null. No
compilation Run is invented. The additive constraint migration preserves existing rows and
checks foreign keys before committing. Older relations without these registrations remain
readable under their legacy contract, but cannot ground a complete support set: evaluation
returns `unknown`. An owner-authorized new relation revision registers current inputs; the
migration does not rewrite historical evidence or infer historical endpoint pins.

Support evaluation uses exact pinned endpoint revisions and the same source freshness and
successor-equivalence checks as compiled knowledge. A changed endpoint invalidates the old
relation as support. Restore preserves the registered edges; missing edges remain explicit
unknown support instead of being reconstructed from current endpoints. Older binaries do
not enforce this support contract and must not be used to qualify these new support sets.

Source maintenance stops before canonical mutation when cumulative old/new fragments exceed
20,000, direct/reverse dependency edges exceed 4,096, or transitive consumer revisions exceed
2,048. `MaintenanceBudgetExceeded` reports `maintenance_budget_exceeded` and
`review_required=true`; owners must narrow or review the maintenance operation. These
execution limits do not enlarge any provider-visible payload budget.

Registered Knowledge Revision lineage is evaluated recursively under the same bounded
visitor before retrieval and projection. A still-active intermediate revision cannot
hide a withdrawn or unsupported compiled ancestor. Cyclic or missing lineage is unknown.
Ordinary source-free data may retain admissible lineage for recall; this does not make it
a complete evidentiary support set. Working checkpoints with declared Knowledge Revision
inputs require both a separate successful task-bound Run and grounded current support.
Query Plan v7 preserves those exact input revision IDs in its checkpoint interpretation;
explicit v6 retains its earlier checkpoint contract. Source/Authority policy is unchanged.
