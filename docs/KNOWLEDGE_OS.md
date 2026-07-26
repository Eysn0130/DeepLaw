# DeepLaw Knowledge OS

Status: implemented baseline for software version `v0.4.0`, reviewed
2026-07-26.

DeepLaw 2.0 compiles source material into review-gated Knowledge Assets and
then compiles only the assets needed for a task into a bounded Knowledge
Capsule. It is infrastructure for an existing Agent host, not a replacement
for Codex, Claude Code, OpenCode, their reasoning loop, or their project
session store.

The Chinese Legal Pack remains a separate trusted domain product. Its
immutable signed-catalog lifecycle and `law_support` server are not replaced
by the general Knowledge Asset vault.

## Corrected product boundary

The reviewed construction plan originally proposed "de-legalizing" the
repository and allowing Agent-facing memory writes. Both choices would weaken
the existing product:

- replacing the Legal Pack would discard its authority, version, temporal,
  source, and immutable-release invariants;
- allowing an Agent to call `remember` or `learn` would convert model output
  and source prompt injection into durable instructions;
- replacing source fragments with semantic atoms would make knowledge
  impossible to reproduce from evidence;
- treating Markdown as canonical would weaken transactions, lifecycle
  constraints, isolation, and auditability;
- automatically trusting a portable package would launder another vault's
  review status.

The implemented architecture is additive:

```text
                               Agent host
                    Codex / Claude Code / OpenCode
                                 |
                 explicit Skill + one read-only MCP leaf
                         /                         \
        knowledge_support                         law_support
               |                                      |
     Knowledge Asset Core                      Chinese Legal Pack
  owner-only mutable vault                 immutable signed releases
  review-gated administration              official/private legal scopes
               |                                      |
 project knowledge, decisions,              statutes, interpretations,
 experience, constraints, tools             legal references and receipts

 Analytix case projects, attachments, chats and case databases stay outside.
```

Installing either plugin does not authorize the other product. Ordinary host
work must not auto-activate either one.

## Knowledge Asset model

An asset is a lifecycle-managed claim or instruction candidate, not an
arbitrary text chunk:

| Field | Purpose |
| --- | --- |
| `kind` | constraint, decision, fact, procedure, rule, experience, lesson, question, or reference |
| `memory_tier` | working, project, experience, wisdom, or domain |
| `statement` | bounded canonical content |
| `semantic_key` | optional identity used for explicit supersession |
| `status` | proposed, active, superseded, revoked, or quarantined |
| `verification` | unverified, source-bound, or human-verified |
| `trust` | untrusted, user-provided, or verified-source provenance |
| `sensitivity` | public, internal, private, or restricted |
| `legal_authority` | always `false` for the general Knowledge Asset core |
| `source_refs` | exact fragment IDs, locators, and quote hashes |
| `content_sha256` | hash of immutable identity-bearing content |

`trust` records provenance; it is not a truth score. DeepLaw does not emit a
synthetic numeric confidence and does not infer approval from model agreement.
`verified_source` is reserved for a future publisher-authenticated Knowledge
Asset channel and is rejected by the current user CLI and store API. It is not
the status of a manually entered statement. `human_verified` means a human
reviewed activation, not that a factual or legal proposition is independently
proven.
Only explicit human review can move an asset to `active`. A semantic key can
have only one active asset, and replacement requires an explicit
`supersedes_asset_id`.

Source fragments remain independent evidence. A Knowledge Asset may summarize
or classify a fragment, but never replaces it.

## Vaults and isolation

Each vault is a physical owner-only directory:

```text
vault/
  vault.json
  vault.sqlite3
  sources/
```

The manifest and SQLite identity must agree. On POSIX systems the directory is
`0700` and identity files are `0600`; a group/world-readable or symlinked vault
fails closed. Separate users, projects, teams, and domains should use separate
vault roots. DeepLaw does not merge them at query time.

The SQLite database contains:

- content-addressed source identities and source fragments;
- lifecycle-managed assets;
- a small reviewed relation vocabulary;
- a full-text discovery index;
- a hash-chained mutation event log.

Every persistent operation and Agent read first reconciles the event inventory
against current Asset lifecycle state, sources, fragments, reviewed relations,
and exact FTS projection. A valid event chain with a directly edited `status`
or search row therefore fails closed. Stable database fingerprints cache a
successful reconciliation; inode, size, mtime, ctime, revision, or audit-head
change forces replay. A pinned reader refuses a changed database instead of
authenticating its old snapshot as the new file. Selected source-bound Assets
also recheck the stored source size and SHA-256, with a file-identity cache to
avoid repeatedly hashing unchanged large files.

The event chain remains tamper-evident, not externally authenticated. Its head
is inside the same vault. A hostile owner able to rebuild the whole vault and
event history can still replace it, so trusted backups or signed distribution
are required for publisher authenticity.

## Knowledge Compiler

`deeplaw knowledge ingest` supports PDF, DOCX, legacy DOC through a local
LibreOffice conversion, UTF-8 text, Markdown, JSON, source code, CSV/TSV, YAML,
TOML, XML, HTML, SQL, and logs.

The Knowledge-specific UTF-8 extractor preserves line structure, blank lines,
and internal indentation for code and structured text. It enforces source,
character, line, and block-count limits before creating fragments. The original
bytes remain separately hash-bound.

Compilation preserves three layers:

```text
source bytes
  -> bounded source fragments with stable locators and hashes
  -> proposed Knowledge Asset candidates
```

Markdown headings can define section boundaries, but Markdown is not the
canonical store. PDF ingestion fails closed when the native text layer requires
OCR unless the operator selects the evidence-preserving document engine.
Legacy DOC conversion records the LibreOffice version and converted DOCX hash;
if LibreOffice is unavailable or conversion is unsafe, ingestion fails.
The compiler hashes the input before extraction, rechecks it after extraction,
and requires the vault copy to match the same size and SHA-256. A concurrently
changed source fails instead of binding extracted fragments to different bytes.

Instruction-like text, invisible controls, and bidirectional controls mark the
source risky and quarantine all of its candidates. This is intentionally
conservative: ordinary manual proposals pass through the same detector.
Activating any quarantined Asset requires both reviewed confirmation and a
separate quarantine-risk confirmation. The Agent cannot promote it.

The compiler requires `--confirm-no-case-data`. This is an explicit product
boundary, not a personal-information detector. Analytix case materials belong
in the case project and must not be copied into a DeepLaw vault.

## Context Compiler

The Context Compiler searches only active, human-reviewed, non-expired assets.
Each read-only vault handle pins one SQLite read transaction, so search,
relations, audit anchor, revision, and exported content come from the same
committed snapshot. It then applies:

1. explicit task and optional goal;
2. optional kind and memory-tier filters;
3. bounded lexical candidate discovery;
4. deterministic admission that rejects a weak one-term match in a longer task;
5. relevance order with a bounded preference for constraints, decisions, rules,
   and procedures;
6. one bounded hop over explicit human-reviewed relations, never free graph
   traversal;
7. current source/integrity verification for every selected Asset;
8. fair item/content allocation so one long Asset cannot starve the remaining
   selected evidence;
9. an eight-reference / 4,000-character provenance-metadata budget;
10. a 64,000-character hard limit for the complete serialized Capsule;
11. explicit selection reasons, reviewed contradictions, and gaps.

It emits `deeplaw.knowledge-capsule/v1`:

```text
task + goal
  -> constraints
  -> decisions
  -> supporting knowledge
  -> experience and lessons
  -> open questions / next actions
  -> source bindings and reviewed relations
  -> explicit budget and gaps
  -> capsule digest + vault audit anchor
```

Weak lexical candidates and excluded source references are counted and surfaced
as gaps; the Agent can use the asset URI for focused verification instead of
receiving an unbounded reference dump. Excerpts are query-aware rather than
prefix-only, must remain exactly reproducible from the current statement, and
compiler parts from one logical section are diversified before filling the
item budget. Capsule verification first enforces the
packaged Draft 2020-12 JSON contract, then covers every field other than its ID
and digest, verifies payload accounting and projected content against the
current asset, checks relations and the historical audit anchor, and rejects
missing, revoked, superseded, expired, or changed assets when the vault is
available. A stale capsule can remain integrity-valid when an unrelated later
mutation occurred; `stale` is reported separately.

Each item records either `lexical_match` or the exact reviewed relation that
admitted it. Open-question text is never copied into an executable
`next_actions` field; actions contain only a fixed review verb and the Asset
URI. The Capsule trust boundary states that general Assets are not legal
authority, case data is forbidden, and authoritative legal-source retrieval
belongs to `law_support`.

Retrieved content is data by default. Only an active, human-reviewed
constraint, rule, or procedure has
`directive_mode=reviewed_instruction`. It still cannot override system,
developer, repository, or current user instructions.

## Memory and learning lifecycle

DeepLaw memory is curated knowledge, not a transcript dump:

| Tier | Intended content | Lifecycle |
| --- | --- | --- |
| working | temporary task state | mandatory expiry |
| project | decisions, facts, constraints, procedures | explicit supersession/revocation |
| experience | failures, fixes, outcomes, lessons | proposed by debugger/feedback, reviewed by human |
| wisdom | stable cross-project patterns | deliberate promotion, never automatic |
| domain | durable references and domain rules | source-bound and separately governed |

`deeplaw knowledge debug` and `deeplaw knowledge feedback` create proposals.
Feedback requires a real Capsule file whose digest, ID, audit anchor, selected
Assets, and current vault binding verify; a shaped Capsule ID is insufficient.
They never self-promote. There is deliberately no Agent-facing `remember`,
`learn`, `approve`, `import`, or delete operation.

Conversation history remains the host's responsibility. DeepLaw should retain
only a reviewed decision, constraint, fact, lesson, or source fragment that is
worth carrying across tasks; it must not mirror every message.

## Relations and human views

The v1 relation set is deliberately small:

```text
supports, contradicts, depends_on, implements,
derived_from, applies_to, related_to
```

Every relation is an explicit human-reviewed edge between active assets.
Self-loops are prohibited and Agent expansion is bounded. Generated graph
extraction and unconstrained multi-hop traversal are not authority paths. A
selected `contradicts` edge becomes an explicit Capsule gap; DeepLaw does not
silently choose a winner.

`deeplaw knowledge export-markdown` produces deterministic Markdown, backlinks,
and a hash manifest for Obsidian or code review. Titles are escaped and Asset
statements are emitted inside dynamically sized literal blocks, so stored text
cannot become an executable Markdown link, embed, or HTML element. The export
is replaceable only when the destination contains a closed DeepLaw manifest,
every tracked file still matches its size and hash, and there is no untracked
user file. A modified or mixed human-notes directory fails closed instead of
being deleted. SQLite remains canonical.

## Portable Knowledge Assets

`deeplaw knowledge export` creates a deterministic `.dlk` package for a fixed
vault revision. It:

- exports active, non-expired assets up to an explicit sensitivity ceiling;
- hashes every payload and all identity-bearing manifest fields;
- recomputes Asset, source, fragment, and relation identities instead of
  accepting a self-consistent ZIP manifest alone;
- rejects unsafe paths, duplicate entries, oversized entries, excessive
  expansion, and invalid record counts;
- can optionally include source fragments and source files.

Relation evidence participates in the package sensitivity boundary. Export
fails if an included relation depends on a source above the selected
sensitivity ceiling; it is never silently leaked or stripped.

Package v1 does not sign publisher identity. Import therefore verifies content
integrity, strips remote trust, and creates local `untrusted` quarantined
proposals. Each asset requires explicit local review before activation.

Publisher signing, revocation, and monotonic update channels are future gates;
the existing Legal Pack signing system must not be casually reused without a
separate Knowledge Asset trust policy.

## Agent interface

The optional Knowledge OS plugin starts:

```text
deeplaw knowledge mcp --stdio
```

It exposes one leaf tool, `knowledge_support`, with five operations:

| Operation | Result |
| --- | --- |
| `search` | at most five short reviewed asset cards |
| `get` | one exact active non-restricted asset |
| `context` | one bounded task-specific Knowledge Capsule |
| `verify` | asset, source-binding, usability, and audit verification |
| `inspect` | sanitized readiness and review backlog without local paths |

The plugin is explicit-only and separate from the Legal Pack plugin. Restricted
assets and inactive proposals are never exposed. Search cards, exact reads,
verification arrays, and complete MCP responses are also bounded; a response
larger than 64 KiB fails closed. The advertised output schema is a closed,
operation-discriminated contract; the Capsule contract is bundled into the
tool schema so hosts do not resolve external references. Search exposes ordinal
rank and hit reason, not an uncalibrated confidence score.

## CLI example

```bash
deeplaw knowledge init \
  --vault ~/.deeplaw/vaults/my-project \
  --name my-project \
  --scope project

deeplaw knowledge ingest \
  --vault ~/.deeplaw/vaults/my-project \
  --source ./ARCHITECTURE.md \
  --source-kind document \
  --sensitivity internal \
  --confirm-no-case-data

# Review the proposed asset, then activate exactly one ID.
deeplaw knowledge approve \
  --vault ~/.deeplaw/vaults/my-project \
  --asset-id asset_... \
  --confirm-reviewed

# Required in addition when the proposal is quarantined:
#   --confirm-quarantine

deeplaw knowledge context \
  --vault ~/.deeplaw/vaults/my-project \
  --task "Implement the storage migration without breaking accepted constraints" \
  --confirm-no-case-data \
  --max-items 8 \
  --max-chars 6000 \
  --output ./capsule.json

deeplaw knowledge verify-capsule \
  --capsule ./capsule.json \
  --vault ~/.deeplaw/vaults/my-project

deeplaw knowledge feedback \
  --vault ~/.deeplaw/vaults/my-project \
  --capsule ./capsule.json \
  --outcome partial \
  --observation "The Capsule exposed one unresolved owner." \
  --lesson "Resolve explicit gaps before execution." \
  --confirm-no-case-data
```

`context` requires the same explicit non-case attestation because the Capsule
persists `task` and `goal`. The flag is an operator boundary, not a content
classifier: Analytix case facts, chats, identifiers, and attachments remain
forbidden even when the flag is present.

Capsule, `.dlk`, and Markdown export paths fail closed instead of overwriting an
unrelated existing file or directory. Markdown replacement additionally
requires a complete, unchanged DeepLaw export manifest.

Set `DEEPLAW_KNOWLEDGE_VAULT` to select the default vault for a host process.
If that vault does not exist, the MCP process still completes capability
discovery but every read fails with a sanitized unavailable error. It never
creates a vault or falls back to another path.

## Verification and release gates

The v0.4.0 baseline is covered by contract, lifecycle, isolation, injection,
database/FTS/source tamper, package, Markdown, Context Capsule, full CLI
lifecycle, MCP stdio, and existing Legal Pack tests. Before release:

```bash
uv lock --check
uv run ruff check .
uv run pytest
python /path/to/plugin-creator/scripts/validate_plugin.py \
  plugins/deeplaw-knowledge-os
python /path/to/skill-creator/scripts/quick_validate.py \
  plugins/deeplaw-knowledge-os/skills/use-knowledge-assets
uv build
git diff --check
```

The held-out protocol and machine claim gate are implemented in
[`EXTERNAL_BENCHMARK_PROTOCOL.md`](EXTERNAL_BENCHMARK_PROTOCOL.md). Real external
runs, hidden labels, and two independent reproductions are still pending. Unit
tests and the public development diagnostic do not establish superiority over
other knowledge systems.

## Deliberate non-goals

- no replacement Agent runtime or IDE;
- no autonomous durable memory writes;
- no automatic wisdom promotion;
- no case-project storage or cross-case access;
- no multi-tenant service claim;
- no generated graph or summary as source authority;
- no vector dump or unbounded graph traversal;
- no publisher-authenticity claim for unsigned `.dlk` v1;
- no claim of universal superiority without reproducible held-out evidence.
