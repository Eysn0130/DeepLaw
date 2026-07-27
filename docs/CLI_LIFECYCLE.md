# DeepLaw Knowledge CLI lifecycle

Status: implemented control-plane baseline on `main`, reviewed 2026-07-27.

This document is the operator walkthrough for the general Knowledge Asset product. It does
not apply to the separate Chinese Legal Pack, and it does not authorize case-private data.
All examples use a dedicated test vault.

## Contract at a glance

```text
source_key
  └── immutable source version
        └── located fragments
              └── proposed / quarantined assets
                    └── exact review manifest + Review Receipt
                          └── active Knowledge Assets
                                └── verified Knowledge Capsule
                                      └── Task Run Receipt
                                            └── Feedback Ledger
                                                  ├── lesson proposal
                                                  └── regression case
```

Persistent administration is local CLI work. `knowledge_support` remains read-only and has no
route to these commands.

## 1. Initialize and diagnose

```bash
CONTROL_ROOT="$(mktemp -d)"
CONTROL_VAULT="$CONTROL_ROOT/vault"

uv run deeplaw knowledge init \
  --vault "$CONTROL_VAULT" \
  --name control-example \
  --scope project

uv run deeplaw knowledge doctor \
  --vault "$CONTROL_VAULT" \
  --permissions
```

On POSIX, the permission report verifies the Vault root, manifest, database, source directory,
and stored source files have no group/world permissions and are not symlinks. On Windows the
report deliberately returns `not_verified`: POSIX mode bits do not prove equivalent NTFS ACL
isolation. Use a dedicated OS identity and native ACL review before treating a Windows Vault as
owner-only.

## 2. Add one logical source

```bash
printf '# Constraint\nEvery release must retain its source digest.\n' \
  > "$CONTROL_ROOT/policy.md"

uv run deeplaw knowledge source add \
  --vault "$CONTROL_VAULT" \
  --source "$CONTROL_ROOT/policy.md" \
  --typed-extraction deterministic-v1 \
  --confirm-no-case-data
```

The result includes:

- an opaque, stable `source_key` derived from the declared logical origin;
- an immutable `source_id` bound to the exact bytes and compiler identity;
- content SHA-256, stored fragment locators, and proposal IDs;
- source status `pending` and `previous_source_id` when this is an update;
- `idempotent=true` when the exact source/compiler identity already exists.

Local absolute paths are not stored in the Vault. Source bytes are copied into the owner-only,
content-addressed source directory before the transaction is committed.

### Directory preflight and ingestion

```bash
uv run deeplaw knowledge source add-dir \
  --vault "$CONTROL_VAULT" \
  --directory ./docs \
  --recursive \
  --include '*.md' \
  --exclude 'archive/**' \
  --dry-run \
  --confirm-no-case-data
```

The manifest contains only relative paths, sizes, and hashes. It is bounded to 100,000 scanned
entries and 10,000 admitted files. A real run uses per-file atomicity: a failed file is reported
without rolling back or corrupting successful source transactions. Background resume/cancel jobs
are not implemented.

## 3. Review an exact proposal set

List candidates and freeze the exact source membership:

```bash
uv run deeplaw knowledge review queue \
  --vault "$CONTROL_VAULT" \
  --source-id source_REPLACE_WITH_EXACT_ID

uv run deeplaw knowledge review manifest \
  --vault "$CONTROL_VAULT" \
  --source-id source_REPLACE_WITH_EXACT_ID
```

Approve only the hash returned by the manifest command:

```bash
uv run deeplaw knowledge review approve-source \
  --vault "$CONTROL_VAULT" \
  --source-id source_REPLACE_WITH_EXACT_ID \
  --review-manifest-sha256 REPLACE_WITH_EXACT_SHA256 \
  --reviewer-id local-operator \
  --reason 'Reviewed every member of this exact source version.' \
  --confirm-reviewed
```

The transaction verifies the source bytes, exact membership hash, proposal states, quarantine
confirmation, and source-version predecessor before activation. It records an immutable local
Review Receipt with schema `deeplaw.knowledge-review-receipt/v1`. The v1 `signature` field is
explicitly `null`; the receipt is hash/audit-chain protected but does not claim an independent
reviewer signature.

Individual approve/reject commands exist for ordinary proposals. An Asset belonging to a
pending successor of an active source cannot be individually approved: that would expose a
partially switched version, so the CLI requires an exact source-level transaction.

## 4. Search, compile, and verify a Capsule

```bash
uv run deeplaw knowledge search \
  --vault "$CONTROL_VAULT" \
  --query 'source digest'

uv run deeplaw knowledge context \
  --vault "$CONTROL_VAULT" \
  --task 'Prepare a release without losing source provenance.' \
  --confirm-no-case-data \
  --output "$CONTROL_ROOT/capsule.json"

uv run deeplaw knowledge verify-capsule \
  --vault "$CONTROL_VAULT" \
  --capsule "$CONTROL_ROOT/capsule.json"
```

Only active, human-reviewed, non-expired, permitted-sensitivity Assets are admitted. Different
sources with the same section title retain separate source identities. Every selected
source-bound item receives at least one compact source reference; if the provenance budget
cannot support another item, the item is not selected.

The Capsule binds the Vault ID, revision, audit head, task, budgets, selected Asset IDs, source
references, gaps, and digest. Verification can be portable or current-Vault-bound.

## 5. Record a task run

```bash
uv run deeplaw knowledge run-receipt create \
  --vault "$CONTROL_VAULT" \
  --capsule "$CONTROL_ROOT/capsule.json" \
  --status partial \
  --host-name codex \
  --host-version local \
  --latency-ms 125
```

Schema `deeplaw.knowledge-run-receipt/v1` binds the verified Capsule, historical Vault anchor,
task/goal hashes, selected Asset/source inventory, host/model identity, timestamps, outcome
artifact hash, and optional token/latency/cost values. Missing metrics remain `null`; the CLI
never invents them. The Store re-verifies the supplied Capsule and derives/cross-checks the exact
identity, digest, revision, audit anchor, Asset IDs, and embedded source IDs; callers cannot create
a receipt by supplying a merely well-formed Capsule ID. A receipt records execution facts, not
proof that the result was correct.

## 6. Record and replay structured feedback

```bash
uv run deeplaw knowledge feedback record \
  --vault "$CONTROL_VAULT" \
  --run-id run_REPLACE_WITH_EXACT_ID \
  --outcome partial \
  --helpful-asset-id asset_REPLACE_WITH_EXACT_ID \
  --missing-knowledge 'The rollback owner is not documented.' \
  --observation 'The provenance constraint was useful.' \
  --recommended-action 'Review a source-bound rollback owner decision.' \
  --confirm-no-case-data

uv run deeplaw knowledge feedback replay \
  --vault "$CONTROL_VAULT" \
  --feedback-id feedback_REPLACE_WITH_EXACT_ID \
  --capsule "$CONTROL_ROOT/capsule.json"
```

Schema `deeplaw.knowledge-feedback-ledger/v1` separates helpful, irrelevant, harmful, stale,
missing-source, missing-knowledge, incorrect-relation, and budget-failure classifications. It
rejects any Asset classification not present in the bound Run Capsule, then creates a review-gated
lesson proposal and a deterministic regression case. Neither is visible to Agents before review.
Replay compares historical and current selection but explicitly sets `task_success_inferred=false`
and `claim_eligible=false`. Its output records original/current Asset and source-version IDs,
selection/source/gap changes, and the fixed compiler/item/character-budget configuration. It does
not turn a retrieval difference into a task-success claim.

## 7. Update a source atomically

After editing the same logical document:

```bash
uv run deeplaw knowledge source update \
  --vault "$CONTROL_VAULT" \
  --source-key sourcekey_REPLACE_WITH_EXACT_ID \
  --source "$CONTROL_ROOT/policy.md" \
  --typed-extraction deterministic-v1 \
  --confirm-no-case-data

uv run deeplaw knowledge source diff \
  --vault "$CONTROL_VAULT" \
  --old-source-id source_REPLACE_OLD \
  --new-source-id source_REPLACE_NEW
```

The successor remains `pending`; the old source and its active Assets remain usable. After the
new exact review manifest is approved, DeepLaw atomically:

1. activates reviewed successor Assets;
2. supersedes matching old semantic identities;
3. revokes old sections absent from the successor;
4. marks the prior source version `superseded`;
5. marks the new version `active`;
6. records source, Asset, review, revision, and audit events.

Any stale predecessor, changed manifest, invalid source hash, or failed review rolls back the
transaction.

## 8. Legacy Vault migration

Existing Vaults without the control-plane tables remain readable. Plan first, then explicitly
apply the additive transaction with a verified backup, verify it, and retain the ability to
restore the exact pre-migration Vault:

```bash
uv run deeplaw knowledge migrate --vault /path/to/vault
uv run deeplaw knowledge migrate \
  --vault /path/to/vault \
  --apply \
  --backup /safe/vault-backup
uv run deeplaw knowledge migrate \
  --vault /path/to/vault \
  --verify \
  --backup /safe/vault-backup
uv run deeplaw knowledge migrate \
  --vault /path/to/vault \
  --rollback \
  --backup /safe/vault-backup \
  --confirm-rollback
```

The migration creates logical source lifecycle records, local receipt ledgers, and one audit
event. It does not rewrite source bytes or existing Asset identities. `--apply` refuses an
unhealthy Vault, creates a consistent owner-only SQLite/source backup, commits its manifest and
inventory hashes, and verifies the result after the transaction. `--rollback` accepts only a
matching verified backup, atomically swaps the restored Vault, and retains the replaced Vault in
a sibling recovery directory rather than deleting it.

## Output and failure semantics

- Command results are JSON on stdout by default; diagnostics and exit status remain separate.
- `deeplaw knowledge --format jsonl ...` emits one compact machine event and
  `deeplaw knowledge --format human ...` emits deterministic human-readable text.
- Large ID inventories and failure lists are bounded and report truncation explicitly.
- No CLI command silently overwrites a Capsule, package, or unowned Markdown directory.
- Persistent operations begin with a full event/state integrity check and use SQLite
  transactions.
- The non-case-data confirmation is an operator declaration, not a DLP classifier.
- `knowledge doctor --permissions` is diagnostic; it does not repair ACLs.

Command-specific tables, native Windows ACL enforcement, resumable background jobs, signed
reviewer receipts, general snapshot/restore commands beyond migration recovery, and URL/Git
adapters are tracked in
[`../ROADMAP.md`](../ROADMAP.md), not advertised as implemented commands.
