# DeepLaw v0.7 construction baseline audit

Audited: 2026-07-27

This is the factual starting point for the v0.7 construction line. It records
what the checked-out product does before Identity v2 and the Retrieval Fabric
are introduced. It is not a capability promise.

## Repository baseline

| Item | Audited value |
| --- | --- |
| HEAD | `e0f1fe3ff01d3026df12673d57c69014c2c4dca4` |
| Branch | `main`, equal to `origin/main` at audit time |
| Package version | `0.6.0` in `pyproject.toml` and `src/deeplaw/__init__.py` |
| Worktree | Clean before construction began |
| Pre-construction quality gate | `uv lock --check`, Ruff, `git diff --check`, and 395 tests passed |
| PR #5 | Merged v0.6 control-plane closure; all five recorded CI jobs passed |
| Candidate manifest | Internal, `claim_eligible=false`, not frozen for external proof |

The PR #5 candidate is bound to commit `aea0c319bdd72c2c5e3ed2a26bd71a97f6fa686f`,
not the current merge commit. Its wheel and sdist hashes are historical v0.6
evidence and must not be reused for v0.7.

## Current supported surface

The v0.6 implementation has a local SQLite Knowledge Vault, content-addressed
stored source bytes, source fragments, proposed/quarantined Assets, explicit
review and activation, source update/remove, an append-only audit chain,
review/run/feedback receipts, bounded Context Capsules, deterministic Markdown
projection, unsigned `.dlk` import/export, and separate read-only Knowledge and
Legal MCP processes.

The default document path handles PDF and DOCX structurally enough for the
v0.6 contracts. Legacy DOC uses an explicit LibreOffice subprocess. Markdown,
text, code, JSON/JSONL, YAML/TOML, CSV/TSV, SQL, XML/HTML/CSS and logs are
accepted as UTF-8 line-preserving text; accepting a suffix is not equivalent to
having a mature structure-aware adapter for that format.

The optional local Discovery Index is implemented but experimental and is not
used by the default Context Compiler or MCP. The Obsidian projection is a
minimal one-way Markdown view, not the requested Wiki/Canvas workbench.

## Default Knowledge retrieval path

The v0.6 path is one lexical route followed by one reviewed-relation expansion:

1. `normalize_text` applies NFKC, whitespace compaction and lower-casing.
2. Chinese runs produce ordered 2-grams, 3-grams and, only for runs up to 12
   characters, the whole run. ASCII produces one token around letters, digits,
   hyphens, underscores, dots and version-like punctuation.
3. At most 32 terms are sampled across a long query. They form one OR-only FTS5
   expression.
4. FTS5 `unicode61` and fixed BM25 field weights retrieve at most 64 rows.
5. Exact semantic-key/title heuristics and fixed kind/review bonuses alter the
   lexical score.
6. Status, expiry, sensitivity and stored-source integrity gates remove
   ineligible rows.
7. The Context Compiler applies another lexical admission threshold, a
   relative-score threshold, and at most one hop of human-reviewed relations.
8. Section-part deduplication, item/character/source-reference/payload budgets
   select Capsule content.

There is no default exact-ID route, phrase/proximity route, fielded query
grammar, stemming, Chinese synonym or variant handling, dense route, tree
route, temporal route, graph planning, fusion profile, reranker, Knowledge
Duties, token budget, or operator explain trace.

## White-box retrieval findings

| Finding | Consequence | Initial failure class |
| --- | --- | --- |
| OR-only CJK 2/3-grams dominate long Chinese input | Common adjacent characters create a large noisy candidate pool | tokenization / fusion |
| The 32-term sampler preserves boundaries but can omit the task's decisive middle terms | Long tasks can miss the actual entity or constraint | query parsing / candidate miss |
| Traditional/simplified variants and typo tolerance are absent | Semantically identical Chinese queries may not meet | tokenization |
| `camelCase` is lower-cased but not segmented; snake/hyphen forms remain different tokens | Code-symbol paraphrases are brittle | tokenization |
| No exact phrase or proximity channel exists | Shared terms can outrank the requested phrase | candidate miss / fusion |
| Fixed kind bonuses are query-independent | Constraints/decisions can outrank a more useful reference | fusion |
| Optional semantic Discovery is a separate operator command | Default `recall` cannot generalize paraphrases | candidate miss |
| Relation expansion is seeded only by lexical hits and has no duty/budget trace | Multi-hop and global queries fail when the first hop misses | graph |
| Time is represented mainly by expiry, not valid/observed/review time | `current` and `as-of` knowledge queries are not supported | temporal |
| A no-hit result is explicit, but weak lexical neighbors can still pass fixed admission | No-answer quality is not established | no-answer / admission |
| Character budgets are exact but tokenizer costs are unknown | Context cannot be compared fairly under a token budget | context budget |

The existing control diagnostic uses 24 synthetic sources, 26 Assets and 20
exact-token questions. It verifies lifecycle mechanics and reports excellent
local latency on that fixture, but it cannot support a claim about Chinese
paraphrase, long-document navigation, temporal or multi-hop reasoning,
no-answer behavior, 100k scale, or competitor leadership.

## CLI friction

The normal path is nested under `deeplaw knowledge`. It requires the operator
to read JSON, copy a `source_id`, generate and copy a review-manifest SHA-256,
and later copy Run/Asset IDs. The README quick start even invokes Python to
extract identifiers. There is no root-level `init/add/review/recall/explain`,
interactive queue, stable alias, `--latest`, resumable job, shell completion,
or local TUI. This fails the requested five-command Golden Path.

## Identity and compiler blockers

- Local `source_key` hashes an absolute path, so moving a project or cloning it
  elsewhere changes identity.
- The current `source_id` mixes bytes with title, trust, sensitivity and the
  complete compiler payload.
- Compiler identity, proposal identity and governance do not have independent
  canonical records.
- The store requires `len(asset_specs) == len(fragments)` and zips one Asset to
  one Fragment during source compilation.
- Section identity depends on heading text and occurrence order. It cannot
  safely distinguish unchanged, modified, split, merge and ambiguous lineage.
- Reviewed relations are Asset-revision triples with optional single-fragment
  evidence. They lack stable relation keys, temporal fields and revision
  lineage.

These are P0 blockers. Retrieval improvements built on the current identities
would make derived indexes faster but not correct.

## Documentation corrections required

- Replace “Verified execution” with “Capsule-bound Run Record”. A receipt binds
  inputs and recorded outcome; DeepLaw does not verify arbitrary execution.
- Do not describe suffix acceptance as mature format support. Structure-aware
  support must be reported per adapter and fixture.
- Do not call the current absolute-path-derived key a move-stable logical
  identity.
- Replace non-standard capability labels such as `Supported (minimal)` with the
  closed status vocabulary.
- Remove the roadmap's multi-tenant/RBAC/SaaS direction. Local single-user is a
  permanent boundary, not a temporary missing feature.
- Keep all cross-system, 100k-scale, Windows ACL and external held-out claims
  pending until the corresponding artifacts exist.

## Construction priority

P0 is Identity v2, additive migration/rollback, many-to-many proposals,
Knowledge Lineage and relation/time lineage. P1 then establishes Source IR and
Source Tree, the multi-channel Retrieval Fabric, Knowledge Duties, token-aware
Capsules and retrieval diagnostics. Golden Path CLI, local workbench,
Inbox/feedback profiles, Wiki/Canvas and Skill Factory follow on the same
service layer. Release status remains v0.6 until the new gates are actually
met.
