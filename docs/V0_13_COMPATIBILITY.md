# DeepLaw v0.13 source-candidate compatibility statement

Status: **not released**. The repository package version remains `0.12.0`; this document describes
the additive v0.13 working-tree contract and must not be read as a published version promise.

## Contract compatibility

| Surface | Current default in the source candidate | Compatibility retained | Boundary |
| --- | --- | --- | --- |
| Semantic compilation | `living-wiki-agent/v3`; repository-visible deterministic development runs use source-run/lifecycle v2 | Profile v1 and v2 compiler inputs remain readable; deterministic lifecycle v1 stays immutable and complete-only | v3 adds dynamic applicability, Statements and exact freshness/verification status; development lifecycle v2 separates mechanical `status` from truthful `semantic_status` and is never formal release evidence |
| Query | Query Plan v6 for `knowledge query`/Python retrieval query | Query Plan v5 remains explicitly selectable | v6 honors and receipts `retrieval_mode`/`graph_hops`/canonical fallback and an optional opaque task binding, discovers ≤20 revisions, then admits ≤512 Statements from only those revisions; it no-answers legacy content without admitted Statements |
| Context | Query Plan v6 for Python `KnowledgeOS.context.compile`, `deeplaw knowledge context`, `deeplaw knowledge autonomy context`, and autonomous MCP `operation=context` | Explicit `query_plan_version=5` only; a task binding is rejected instead of discarded on v5 | v6 uses the shared domain assembler and additive local Capsule v3 (≤262,144 bytes); exact task binding gates working checkpoints, while nested Provider v2 (≤65,536 bytes) excludes the binding; no ordinary query/context writes the Canonical Ledger |
| Expansion | expansion Profile v2 | v1 receipts remain valid | v2 removes benchmark-shaped aliases, binds profile/lexicon/configuration digests, and keeps aliases additive to the bounded source query; Query Plan v6 records only bounded identity-anchor count/digest/truncation |
| Projection | `standard` Profile, Living Wiki manifest v2 paired with Registry/Link/Resolver manifest v3 | aggregate manifest v1 and Profile `full` remain readable | `standard` removes per-object Canvas; profile changes clean only verified owned files; revisions above 64 Statements use derived Statement Evidence shards without changing canonical identity |
| Evidence grounding | Statement/Evidence core v1 | source/object references remain readable | statement-bearing Profile v3 content must pass exact map/receipt verification |
| MCP read surface | default `query_plan_version=6`; recommended `query/context/wiki/source/verify` | Explicit v5 MCP output/v3 with Capsule v2 and Query Plan v5; scheduled `search`/`recall`/`wiki_lookup` routes remain | `knowledge_support` stays read-only; Provider content is ≤64 KiB and never carries the full plan, candidate scores, rejected-candidate text, or local audit internals |
| MCP mutation surface | unchanged separate `knowledge_sink` | existing owner grants remain subject to their exact allowlist | no read operation hides a write or widens a Grant |
| Knowledge Graph view | graph view v1 with independent `selection_truncated` and `candidate_scan_truncated` budget signals plus bounded gaps | existing node/relation/rejected fields and the 500-admitted / 5,000-scanned hard bounds are unchanged | an inferred complete result is no longer allowed when another admitted Relation or an uninspected candidate tail exists; Wiki local graph and CLI/MCP use the same domain response |
| Agent context | host-neutral Agent Context Envelope v1 | Editor Context Envelope v1 remains accepted for editor-specific context | both are ephemeral; neither is evidence or canonical knowledge |
| Python context lifecycle | lazy persistent snapshot for repeated `KnowledgeOS.context.compile` calls on one handle | startup verification and one-shot retrieval/source/wiki behavior remain | warm context checks bounded identities; state change reopens fail-closed; `close`/context manager releases the snapshot |
| Legal Pack | unchanged isolated `law_support` store/process | current signed release and private-reference compatibility remain | v0.13 Navigator is a source-free read-only view and never creates Official prose |

## Persistence and forward migration

The candidate adds tables and markers in place; it does not rewrite v1/v2 records:

- `deeplaw.semantic-compilation-core/v1` remains the existing semantic persistence marker; Profile
  v3 uses additive versioned artifacts and records through the existing compilation transaction;
- `deeplaw.deterministic-semantic-source-run/v2` and
  `deeplaw.deterministic-semantic-lifecycle/v2` are repository-visible, no-model development
  receipts. A mechanically successful lifecycle may truthfully be `semantic_status=partial`; the
  v2 aggregate is derived from its run rows using `blocked > unknown > partial > complete` and is
  fixed to `formal_release_evidence_ready=false`. Historical v1 receipts remain complete-only and
  are not rewritten or widened to admit v2;
- `deeplaw.statement-evidence-core/v1` explicitly identifies Statement, map, receipt and dependency
  persistence;
- Living Wiki v3 Registry/Link/Resolver artifacts are derived and rebuildable; they are not a
  second canonical database;
- Query Plan v6, local Knowledge Capsule v3, nested Provider v2, and Agent Context Envelope v1 are
  request/response contracts and add no canonical persistence. The local Query Trace is bounded,
  redacted, process-local, TTL/LRU managed, integrity-checked, and owner/runtime deletable; it is
  not a durable receipt database.
- The additive `deeplaw.task-context-binding/v1` object is stored only in existing Run Record
  metadata and its receipt/event binding; no physical migration is needed. Legacy unbound Runs
  remain verifiable but cannot establish current working-checkpoint lineage. New working memory
  requires a task-bound Run. Query Plan v6 and local Capsule v3 bind the exact selector or explicit
  absence, while Provider v2 excludes it. See `docs/V0_13_TASK_CONTEXT_BINDING.md`.

Query Plan v6 narrows a previously defective behavior: accepted retrieval controls can no longer
be ignored, and Statement discovery no longer scans the first 5,000 globally ordered rows before
matching. The plan and receipt bind effective controls, upstream discovery digests/channels,
limitations and the 512-candidate bound. Invalid retrieval modes fail at the shared Python seam,
which also covers CLI/MCP calls after their own closed validation.

Query Plan v6 also retains the bounded normalized source query when expansion aliases are applied;
aliases are additive and cannot erase CJK or non-ASCII names. Its entity-lexicon-free identity
anchor fields bind bounded, case-folded phrase hints used only for discovery/reranking.
Natural-language capitalization, Title Case or punctuation cannot turn such a hint into an
eligibility or admission rule. Ordinary Statements pass an independent case-folded lexical floor;
governed title/alias terms may establish lexical relevance, but inferred casing cannot. Scope,
Authority, lifecycle and temporal checks remain separate. Explicit `semantic_key`, `knowledge_id`
and `revision_id` targets remain strict admission constraints. The plan records only
`identity_anchor_count`, `identity_anchors_sha256`, and `identity_anchors_truncated`; anchor text is
never emitted in a receipt, and ambiguous matching aliases remain multiple candidates rather than
being silently merged.

The graph response change is additive and non-persistent. `selection_truncated=true` means at least
one additional governance-admitted Relation was observed after the requested result limit;
`candidate_scan_truncated=true` means the 5,000-candidate scan ended with an uninspected tail. The
runtime may inspect only until the first extra admitted Relation to establish selection truncation;
it never returns that extra Relation and never scans beyond 5,000. Candidate scan and selection
truncation have separate bounded gaps, while a tail containing only governance-rejected Relations
does not falsely report admitted selection truncation. Selection may stop after observing the first
extra admitted Relation without claiming candidate-scan truncation; a candidate-scan gap is emitted
only when the actual configured scan bound is reached, and the gap records that bound rather than a
hard-coded display value.

The repository-visible Semantic runner now emits an additive
`deeplaw.semantic-context-outcome/v2` development report. It treats `deeplaw knowledge context`
with Query Plan v6 as the primary Agent outcome surface and keeps Query Plan v5 `knowledge query`
inside the historical mixed v1 compatibility report as a non-qualifying operator diagnostic.
Owner-local Capsule v3 bytes, Provider Capsule v2 bytes, complete MCP
tool-result bytes, selected Provider content bytes and transport metadata bytes are separate
fields. UTF-8 bytes are never named tokens: the only token value without a real tokenizer or
Provider usage receipt is explicitly `utf8_bytes_div_4_estimate`. Token savings and
distractor-induced delta remain `not_executed` until a frozen equal-duty, equal-budget comparator
exists. The historical query-run v1 contract remains readable, but neither v1 nor v2 development
evidence is qualification eligible.

The MCP `audit` projection is local-only. Provider delivery is reduced to `standard` and uses only
the opaque `receipt_id` for a redacted explain lookup. The runtime trace is ephemeral (16 entries,
15-minute TTL, 256 KiB per entry, 1 MiB total), integrity-checked and cleared on Vault identity
change or process close. It is not a new persistent database, does not write the canonical Ledger,
and intentionally does not promise cross-process receipt retention. The v6 local Capsule v3 audit
summary excludes candidate scores, rejected-candidate text, SQL/cache/parser diagnostics, local
paths, credentials, and hidden reasoning.

The executable migration regression simulates a pre-Statement v0.12 database, reopens it through
the additive installer, verifies preserved audit heads, verifies both stores, snapshots the Vault,
restores it, and confirms v5 plus the new Statement marker. Formal G04 still requires the exact
release wheel and rollback artifacts; that release-bound gate is `not_executed`.

## Host compatibility

- Codex and Claude Code package the same six explicit split Skills. Claude lifecycle hooks are
  opt-in, no-model, bounded, network-free by code path and do not install themselves.
- OpenCode defaults to `deeplaw-query` and its compiler profile to `deeplaw-compile-source`; the
  two legacy wrapper names are not current defaults.
- Obsidian targets the public plugin API package `1.13.1` and exact upstream API commit
  `cc1744324150c632416857c98964f87b1574a5fc`. Its CLI bridge has closed parsers and no manual-ID
  core picker flow; real desktop E2E remains `not_executed`.
- Tolaria targets `v2026-07-22`, commit
  `e2cd718a518cc96d1081b6ec3aabefe3b6c77199`. Its external MCP boundary is supported, while the
  missing third-party preview/promotion UI seam keeps the product result `integration_limited`.

## Rollback and downgrade

Before any future release upgrade, the owner must create and verify a Vault snapshot. A downgrade
must use that snapshot or the prior exact release artifact; it must not delete additive tables or
rewrite audit history in place. Derived Wiki/index artifacts may be discarded and rebuilt by the
selected older compatible runtime. Query callers may explicitly request v5 while the v6 path is
being evaluated, but Profile v3 Statement-bearing writes must not be represented as v2 semantic
completeness.

## Not verified

Linux/macOS/Windows × Python 3.11/3.12/3.13, fresh-wheel migration, exact release rollback,
reproducible wheel/sdist, SBOM, provenance, public re-download, real Codex blind tasks, Human Gold,
real Obsidian desktop E2E and real Tolaria desktop integration remain external or release-bound
gates. No compatibility claim in this document converts any of them to `pass`.

The repository-visible deterministic Semantic Gold remains development evidence. Profile v3 can
complete every canonical transaction and verify the Vault while reporting partial duty coverage;
its lifecycle v2 result may be used to expose query regressions, but cannot satisfy Human Gold,
real-Host, legal, commercial-release, or competitive-claim gates.

## Qualification disposition

The pre-remediation Context v6 statement associated with baseline
`ddfcc36669236716700e49816fb29b05532020e9` was dynamically disproved and is superseded by the
focused [Context v6 parity report](V0_13_CONTEXT_V6_PARITY_REPORT.md). The candidate remains
unreleased: `release_gate_passed=false`, `claim_eligible=false`, and
`competitive_claim_eligible=false`. No real-host, Human Gold, Legal Pack, scale, cross-platform,
or final artifact gate may be inferred from the focused parity results.
