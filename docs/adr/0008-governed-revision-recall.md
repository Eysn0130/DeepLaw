# ADR 0008: ordinary governed revision recall

Status: implementation candidate; not qualification evidence.

The public Sink can activate an ordinary source-free procedure while the default
Query Plan v6 returns no content: discovery admits its revision, but subsequent
selection requires compiled Statements. This violates the ordinary governed
knowledge journey under PRD-KNOW-001..008 and PRD-CTX-001..009. The regression is
`tests/test_governed_revision_recall.py` using synthetic, public data.

Query Plan v7 reuses the same discovery, governance and selection kernel. It adds
an explicit `knowledge_revisions` partition for ordinary source-free, tentative
revisions without compiled Statements. These are data, never evidence or
instructions. They retain their exact identity, body digest, unknown freshness,
verification and immutable revision. They cannot satisfy source-only duties.
Working memory remains subject to task-line admission and cannot use this path.

The existing twenty-revision discovery bound and combined 512-candidate bound
remain. Revisions consume the existing knowledge item/character/estimated-token budget after
Statement selection, rather than acquiring an additional budget. Exact reads
resolve the same current revision and check its complete body before pagination.
Knowledge corrections, forgetting and revocation invalidate later reads; old
Capsules remain immutable snapshots whose verification detects a changed head.

This is an explicit wire change: Query Plan v7, retrieval v4, local Capsule v4,
Provider Capsule v3, inner projection v2, support input v9/output v8. Explicit v6
query/context remains compatible, with its original response shapes and without
the new partition. Historical schemas remain unchanged. The new inputs do not
grant writes, widen scope, or change any existing hard byte ceiling.

No canonical table, mutation shape or migration is added. Existing CAS/Ledger
revisions, recovery, audit replay and grant/idempotency paths remain the durable
representation. An older runtime can still read stored revisions, but cannot
consume the new Capsule shape; rollback must regenerate delivery snapshots using
its supported explicit version, never relabel new bytes as an old contract.

Rejected alternatives: manufacturing a Statement would invent compilation
semantics; routing ordinary knowledge as checkpoints would bypass task policy;
silently selecting v5 would hide the default contract gap. A second retrieval
engine or memory database is unnecessary.

Qualification consumers must bind the new versions to a fresh exact artifact.
Passing the public synthetic regression does not qualify a Host or a release.

Query Plan v7 enforces the local mixed-CJK token estimate across selected Statement,
ordinary-revision and evidence content. Exact evidence that cannot fit is withheld,
not truncated into a satisfied duty. These estimates do not claim Host billing-token
measurement; the separate byte limits continue to bound the complete wire payload.

The v7 selection explanation covers admitted Statements, ordinary revisions and
source passages actually considered by the bounded reader. It exposes at most
twenty identifiers/reasons inline, a digest of the retained selection entries,
and the existing receipt identity for `explain`. Query Audit Receipt v2 retains
at most 1,024 entries in the existing byte-bounded, expiring MCP trace cache.
It contains no rejected identities, source text, titles or rejected counts.
Discovery is explicitly non-exhaustive; a truncated retained inventory is marked.
Query Audit Read v2 returns twenty entries per page. Its `next_receipt_id` is
signed with a process-local secret and bound to the receipt digest. Tampering,
another process, eviction, expiry or a changed read identity invalidates it.
The continuation does not return content or grant additional admission rights.
Explicit v6 retains its original audit contract and behavior.
