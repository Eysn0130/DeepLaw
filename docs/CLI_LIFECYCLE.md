# DeepLaw Knowledge CLI lifecycle

Status: released `v0.7.0` surface, reviewed 2026-07-28. Examples keep canonical writes
in the offline CLI; Agent MCP remains read-only.

## Golden Path

Install the formal wheel once:

```bash
uv tool install https://github.com/Eysn0130/DeepLaw/releases/download/v0.7.0/deeplaw-0.7.0-py3-none-any.whl
```

The complete normal loop uses five user commands and no internal-ID parsing:

```bash
deeplaw init ./vault --name my-project
deeplaw add ./docs --vault ./vault --confirm-no-case-data
deeplaw review --vault ./vault --interactive
deeplaw recall "Which current constraints govern this task?" \
  --vault ./vault --confirm-no-case-data --output capsule.json
deeplaw explain --vault ./vault --last
```

`recall` automatically creates a Query Plan and Retrieval Trace, compiles the Capsule, writes the
bounded local “last” sidecars, and returns `capsule_verification`. It does not ask the operator to
copy a `source_id`, `asset_id`, or manifest hash.

Root commands are:

```text
init · add · sync · review · recall · explain · feedback · status · doctor · open
```

Every command supports deterministic human output or stable machine output where applicable;
advanced control remains under `deeplaw knowledge ...`.

## 1. Initialize and diagnose

```bash
deeplaw init ./vault --name project --scope project
deeplaw status --vault ./vault --jobs
deeplaw doctor --vault ./vault
```

Initialization creates owner-only SQLite, source storage, audit and Identity v2 tables. It refuses
an unsafe/symlink root. POSIX verifies owner-only modes. Windows initialization applies native ACL
hardening and verifies owner SID, broad Users/Everyone access, inheritance, and reparse points.
Final Windows support remains externally pending until the Windows CI run is retained.

`doctor` checks canonical integrity, source hashes, audit/Identity snapshots, permissions, orphaned
derived files, backup state, jobs, and rebuildable indexes. Read-only inspection does not create
operations, derived, profile, or Inbox directories.

## 2. Add sources through resumable jobs

```bash
deeplaw add ./guide.md --vault ./vault --confirm-no-case-data
deeplaw add ./docs --vault ./vault --recursive \
  --include '*.md' --include '*.pdf' --exclude 'archive/**' \
  --confirm-no-case-data
```

`add` first records a closed v2 job manifest, then runs bounded file transactions. State and
progress are durable JSON with exact source/config hashes. Historical v1 jobs are validated and
normalized to v2 when resumed. A crash leaves resumable work rather than a partially declared
success.

```bash
deeplaw add --vault ./vault --resume ingestjob_REPLACE
deeplaw add --vault ./vault --retry ingestjob_REPLACE
deeplaw add --vault ./vault --cancel ingestjob_REPLACE
deeplaw status --vault ./vault --jobs
```

`--dry-run` produces the plan without canonical ingest. Failures are explicit per source; a failed
file does not corrupt prior successful transactions.

One-shot connector snapshots use the same v2 resumable job and review path without asking for a
Source or Asset ID:

```bash
# Pure preflight: validates the URL and bounds, but performs no DNS/network access or Vault write.
deeplaw add --url https://example.org/guide.md --dry-run \
  --vault ./vault --confirm-no-case-data

# One explicitly authorized HTTPS capture. Supplying the publisher hash is recommended.
deeplaw add --url https://example.org/guide.md \
  --expected-sha256 SHA256_REPLACE --max-download-bytes 67108864 \
  --vault ./vault --confirm-network --confirm-no-case-data

# One exact commit from an existing local repository; no clone, checkout, or lazy fetch.
deeplaw add --git-repository ./repo --git-revision FULL_COMMIT_REPLACE \
  --git-repository-id product-docs --include '*.md' --exclude 'archive/**' \
  --vault ./vault --confirm-local-repository --confirm-no-case-data
```

HTTPS accepts only canonical public-DNS TLS on port 443 with no credentials, query, fragment, IP
literal, private/mixed DNS answer, compressed response, or more than five redirects. Every redirect
is resolved and pinned again; the body is capped at 64 MiB and committed by SHA-256. HTTPS
snapshots are always `untrusted`. The Git connector requires a complete 40- or 64-hex commit object
ID and a stable non-secret `--git-repository-id`; its canonical origin contains that ID, commit,
and repository-relative path, never the absolute local path.

Connector snapshots live in owner-only operator state and are reverified before compilation.
Neither connector is registered by `sync --watch`; capture a new snapshot explicitly to observe an
updated URL or commit. Neither connector is callable through MCP, and both still produce only
proposed/quarantined knowledge until human review.

Source formats and compiler controls include:

```bash
deeplaw add ./source.docx --vault ./vault \
  --typed-extraction deterministic-v2 \
  --confirm-no-case-data

deeplaw add ./source.pdf --vault ./vault \
  --pdf-fallback document-engine \
  --confirm-no-case-data
```

Local/external model compilation requires an exact extractor manifest. External mode additionally
requires `--confirm-external-disclosure`. Model output remains proposal/quarantine only.

## 3. Sync, watch, rename, and update

Registered roots can be synchronized once or watched locally:

```bash
deeplaw sync --vault ./vault
deeplaw sync --vault ./vault --watch --interval 2
```

Sync detects new, changed, removed, renamed, and moved logical sources. Source bytes form immutable
revisions; a change creates a pending successor. The active predecessor stays usable until the
exact successor proposal set passes atomic review. Rename/move records explicit Identity mappings
instead of silently creating unrelated knowledge.

Advanced source control:

```bash
deeplaw knowledge source list --vault ./vault --active
deeplaw knowledge source show --vault ./vault --alias docs/architecture.md --latest
deeplaw knowledge source diff --vault ./vault --alias docs/architecture.md --latest
deeplaw knowledge source verify --vault ./vault --alias docs/architecture.md --active
```

The stable `--alias` is a normalized logical path. Any historical path resolves the same Source
Identity after a reviewed rename/move; an alias reused by multiple identities fails as ambiguous
instead of guessing. `--active` selects the reviewed active version and `--latest` may inspect a
newer pending version. Multiple parallel pending successors also make `--latest` fail closed.
Exact IDs and source keys remain available for automation.

## 4. Review exact proposal membership

```bash
deeplaw review --vault ./vault --interactive
```

Interactive review displays the current queue and exact evidence. Approve/reject is applied through
the same review service as the Workbench. A changed queue or source cannot reuse a stale review
commitment.

For automation with deliberate human confirmation:

```bash
deeplaw review --vault ./vault --approve-all \
  --reviewer-id local-operator \
  --reason 'Reviewed exact source and proposal membership.' \
  --confirm-reviewed
```

Quarantined content needs `--confirm-quarantine`. A pending successor cannot be individually
approved because that would break atomic replacement. Review creates an immutable local receipt;
current receipts are hash/audit protected but unsigned.

After an approved source successor changes relation endpoints or evidence, Golden review creates
an inactive relation successor proposal. Unchanged endpoints are labelled `carry_forward`;
modified/renamed/moved endpoints are labelled `full_review`. Neither path inherits approval.
Interactive review displays and decides the new relation candidates immediately. Non-interactive
`--approve-all` deliberately leaves candidates created during that invocation pending, so a second
review invocation is an explicit relation decision and still requires no copied IDs:

```bash
deeplaw review --vault ./vault --approve-all --confirm-reviewed
deeplaw review --vault ./vault --approve-all --confirm-reviewed
```

The first command atomically approves the source and creates relation proposals; the second reviews
the already-visible relation queue. Deleted endpoints remain blocked and absent from the current
graph. Advanced machine-readable control is also available:

```bash
deeplaw knowledge relation carry-forward --vault ./vault
deeplaw knowledge relation carry-forward --vault ./vault --apply
deeplaw knowledge relation candidates --vault ./vault
deeplaw knowledge relation review-candidate --vault ./vault \
  --relation-revision-id relationrev_REPLACE --decision approve \
  --confirm-reviewed
```

Cross-key split, merged, and ambiguous Knowledge Lineage mappings have a separate source-bound
review operation. This advanced JSON surface deliberately uses exact revision-compatible Asset IDs;
the Workbench `map` panel provides the ordinary no-ID path by visible row number:

```bash
deeplaw knowledge lineage --vault ./vault --map-status split \
  --from-asset-id asset_PREDECESSOR \
  --to-asset-id asset_SUCCESSOR_A --to-asset-id asset_SUCCESSOR_B \
  --reviewer-id local-operator --reason 'Reviewed source-bound split.' \
  --confirm-reviewed
```

The mapping records exact refs and review evidence under every involved Knowledge Key. It neither
creates nor activates knowledge, and approval is never inherited. Relation carry-forward detects
these reviewed cross-key mappings and blocks the affected endpoints from the current graph.

## 5. Recall and Explain

```bash
deeplaw recall "What changed and which exception still applies?" \
  --vault ./vault --mode hybrid --max-tokens 4096 \
  --confirm-no-case-data

deeplaw explain --vault ./vault --last --format json
```

Modes are `auto`, `exact`, `lexical`, `semantic`, `tree`, `graph`, `temporal`, `global`, and
`hybrid`. `auto` chooses channels from intent; Dense never enters the default path without an exact
index and model bundle. A normal lexical miss may trigger bounded one-edit ASCII typo repair;
reviewed graph expansion is capped at two hops. Both the Dense index and a pinned reranker are
opt-in:

```bash
deeplaw recall "release rollback procedure" --vault ./vault \
  --mode semantic --discovery-index ./derived/discovery \
  --model-root ./models/discovery --threads 2 \
  --confirm-no-case-data

deeplaw recall "release rollback procedure" --vault ./vault \
  --mode hybrid --reranker-manifest ./local-reranker.json \
  --confirm-no-case-data
```

The reranker can only permute bounded candidates. Explain reports channel candidates, ranks,
exclusions, source and Knowledge Duty coverage, gaps, and budgets; it does not expose a score as
confidence or authority.

Exact as-of retrieval is explicit:

```bash
deeplaw recall "What was current then?" --vault ./vault \
  --mode temporal --as-of 2026-07-01T23:59:59Z \
  --confirm-no-case-data
deeplaw knowledge relation list --vault ./vault --mode as-of \
  --as-of 2026-07-01T23:59:59Z
```

Temporal matching never claims factual or legal applicability.

## 6. Capsule-bound Run Record and feedback

The simple feedback command binds the last verified Capsule, creates a Capsule-bound Run Record,
and records structured feedback without inferring success:

```bash
deeplaw feedback --vault ./vault --outcome partial \
  --missing-knowledge 'Rollback ownership is absent.' \
  --observation 'The storage constraint was useful.' \
  --recommended-action 'Review a source-bound owner decision.' \
  --confirm-no-case-data
```

Feedback can produce a quarantined lesson proposal and a source-free regression case. Helpful,
irrelevant, harmful, stale, missing-source, incorrect-relation, and budget signals remain bound to
the Run/Capsule inventory. Command completion is not task success.

## 7. Operator Workbench and projection

```bash
deeplaw open --vault ./vault
deeplaw open --vault ./vault --obsidian --print-uri
```

On a TTY, `open` launches the local curses Workbench. In non-TTY environments it prints the same
bounded snapshot as JSON. It opens no network listener. Panels cover sources/tree/diff, review,
search/recall/explain, visible-row cross-key Lineage mapping, relations/history, Capsule, feedback,
health, and benchmark status.

The Obsidian path exports deterministic Markdown and JSON Canvas. Reverse edits use the advanced
projection command to produce a diff and quarantined proposal; they never overwrite active state or
inherit approval.

## 8. Inbox and Skill Factory

```bash
deeplaw knowledge inbox list --vault ./vault
deeplaw knowledge inbox promote --vault ./vault \
  --artifact-id inbox_REPLACE --confirm-reviewed

deeplaw knowledge skill build --vault ./vault \
  --output ./skill-bundle --name release-knowledge \
  --description 'Read-only reviewed release knowledge.' \
  --knowledge-key knowledge_REPLACE
deeplaw knowledge skill verify --bundle ./skill-bundle --vault ./vault
```

Use `--help` for exact subcommand arguments. Inbox promotion records the exact artifact bytes as an
untrusted Source Revision and creates an Identity-v2-bound quarantine. Skill bundles pin knowledge,
source refs, budgets, files, and tests. External skills install to quarantine and contain no
management command.

## 9. Snapshots, restore, GC, and forgetting

```bash
deeplaw knowledge snapshot create --vault ./vault --output ./snapshot
deeplaw knowledge snapshot verify --snapshot ./snapshot
deeplaw knowledge snapshot restore --snapshot ./snapshot \
  --vault ./restored-vault --confirm

deeplaw knowledge gc --vault ./vault --dry-run
deeplaw knowledge gc --vault ./vault --no-dry-run --confirm

deeplaw knowledge forget --vault ./vault --asset-id asset_REPLACE \
  --reason 'Explicit local operator request.' --confirm
```

Snapshot creation verifies source/database/audit state. Restore targets a new or explicitly
accepted destination and validates before handoff. GC only removes recognized derived temporary
orphans within bounds. Forgetting removes current retrieval eligibility and current endpoint
relations while retaining verifiable audit history and source bytes unless a separately authorized
retention operation exists.

## 10. Legacy migration and rollback

```bash
deeplaw knowledge migrate --vault ./vault
deeplaw knowledge migrate --vault ./vault --apply --backup ./verified-backup
deeplaw knowledge migrate --vault ./vault --verify --backup ./verified-backup
deeplaw knowledge migrate --vault ./vault --rollback \
  --backup ./verified-backup --confirm-rollback
```

Migration is additive and requires a verified pre-apply backup. Identity v2 migration preserves
legacy source fragments, reviewed status, source order, and audit history. Failure rolls back the
transaction; explicit restore remains available.

## Output and failure semantics

- `--format human|json|jsonl` is stable for supported commands.
- `--quiet` suppresses successful human output; errors remain on stderr.
- `--no-color` keeps automation deterministic.
- validation, integrity, permission, stale membership, disclosure, budget, and policy errors are
  non-zero exits.
- read-only operations do not create canonical or derived state except Golden recall's explicitly
  documented bounded “last Capsule/Trace” sidecars.
- no command silently falls back to model memory, web search, another Vault, or the Legal Pack.
- shell completion is emitted by `deeplaw completion bash|zsh|fish`.
