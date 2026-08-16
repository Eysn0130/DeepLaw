# DeepLaw v0.13 Living Wiki and knowledge-network qualification

Status: **source-candidate qualification only** (2026-08-08). This report records local
deterministic evidence and its boundaries. It is not release, RC, GA, or competitive evidence.

The qualification state remains:

```text
release_gate_passed=false
claim_eligible=false
competitive_claim_eligible=false
```

No provider, credential, real host, cross-OS, Human Gold, signed Legal Pack, or publication gate
was executed in this Wiki continuation.

## Scope and evidence boundary

The intended governed chain is:

```text
Source bytes
-> Source Revision / Fragment / Locator
-> Knowledge Revision / Statement / Relation Revision
-> Ledger current pointer / immutable history
-> rebuildable FTS / Dense / Graph
-> Page Registry / Link Index / Resolver
-> Wiki pages / backlinks / outlinks / bounded views
-> Query Plan / bounded Knowledge Capsule
```

The local fixtures are synthetic or development material. They are not a frozen external identity
Gold, legal corpus, 10k/100k Relation lane, or 10k/100k Wiki qualification. A test that constructs a
100,000-line source string only proves the source-text generator remains bounded; it is not counted
as a 100,000-object Relation or Wiki run.

The focused audit command was:

```bash
uv run --frozen pytest -q \
  tests/test_v013_wiki_network_qualification.py \
  tests/test_v013_wiki_coverage_spec.py \
  tests/test_v013_wiki_link_index.py \
  tests/test_v013_wiki_registry.py \
  tests/test_v013_wiki_resolver.py \
  tests/test_v013_wiki_page_families.py \
  tests/test_v013_persistent_wiki_source.py \
  tests/test_v013_projection_incremental.py \
  tests/test_v013_projection_ownership.py \
  tests/test_v013_projection_recovery.py \
  tests/test_v013_projection_v3_integration.py
```

Result: exit 0 with three explicit skips (`wrong_merge`, `alias_collision`, and the duplicate
Wiki-cycle fixture). A skip is not a pass and remains outside the qualification claim.

## Implemented / partial / not_executed

| Area | Status | Evidence and boundary |
| --- | --- | --- |
| 12 Knowledge kinds | `implemented` | The autonomous v3 mutation, Page Registry, Resolver and kind-page fixtures cover `claim`, `concept`, `entity`, `event`, `decision`, `procedure`, `experience`, `preference`, `synthesis`, `comparison`, `skill`, and `memory`. This is local development evidence. |
| 15 v3 Relation predicates | `implemented` | `src/deeplaw/knowledge_autonomy.py::RELATION_PREDICATES` and `contracts/knowledge-relation.v3.schema.json` have the same 15-value set: `alias_of`, `applies_to`, `consolidates`, `contradicts`, `contributes_to`, `depends_on`, `derived_from`, `describes`, `implements`, `mentions`, `related_to`, `reports`, `same_as`, `split_from`, `supports`. |
| Legacy Relation compatibility | `partial` | The legacy `knowledge_store.py` runtime exposes seven predicates, while `knowledge-sink.output.v1` exposes twelve and omits `alias_of`, `consolidates`, and `split_from`. This is a compatibility-surface limitation; it is not evidence against autonomous v3 parity. |
| Stable identity, aliases and duplicate collapse | `partial` | Local normalization, ambiguity handling, semantic-key reuse, `same_as`/merge/split lineage, and ledger lint are exercised. Cross-language aliases use only a bounded synthetic lexicon. |
| Wrong merge / alias collision quality | `not_executed` | The existing negative fixtures are explicitly skipped; no frozen external identity Gold or independent scorer was supplied. |
| Revision, rename/move, source successor and lifecycle | `implemented` | Existing canonical regressions cover current/history pointers, rename/move identity preservation, withdrawal/forget, source successor handling and selective source-dependent invalidation. |
| Graph hops 0/1/2 | `implemented` | The deterministic smoke graph checks seed-only, direct-edge and two-hop behavior. |
| Hub, deep chain, cycle, contradiction, temporal, dangling and self-loop | `implemented` | These are exercised at smoke scale through the governed Relation fixture; they are not large-Relation evidence. |
| Relation tail-edge position independence | `partial` | The bounded smoke runner records tail/hub/deep checks, but no 10k/100k Relation corpus was executed. |
| 500/5,000 truncation and 10k/100k Relation scale | `not_executed` | No safe audited bulk Relation constructor was available; the owner mutation limit was not weakened. Candidate/Provider bounds remain code contracts, not scale results. |
| Explainable Relation Path API | `not_implemented` (outside the current v0.13 contract) | No real path-query API exists in the current v0.13 contract. `local_graph` and Canvas are derived navigation views, not Relation Path proofs. |
| Coverage Spec | `partial` | `wiki_coverage.py` is a pure validation kernel with closed duties/gaps and deterministic bounds. It is not a projector or read-service integration. |
| Guides / Codemap | `not_implemented` (outside the current v0.13 contract) | No verified integration into projector, Page Registry, Link Index, Resolver, CLI or MCP exists in v0.13. |
| Page Registry / Link Index / Resolver | `implemented` | Manifest-declared v3 components, bounded indexed backlinks/outlinks, stable identity resolution, tamper checks and no-scan reads are covered by local regressions. |
| Recent Changes public seam | `implemented` (bounded current projection) | Before remediation, Python/CLI/MCP returned `deeplaw.living-wiki-browse/v1` current-object results; the focused regression reproduced three failures. The repaired seam returns additive `deeplaw.living-wiki-recent-changes-read/v1` and passed six focused tests. |
| Recent Changes complete history | `partial` | The read path admits the verified index and returned shards through Bundle → Resolver → Registry → bounded `read_page`; binds actual index/shard hash, byte size and event count; validates frontmatter `audit_head`; applies scope/sensitivity admission; exposes `truncated`, `truncation_reason` and projector `history_truncated`. The projector retains only its newest 10,000 events. There is no complete-history cursor, object diff, or `as_of` Wiki view. |
| Filesystem isolation for Recent Changes | `implemented` | The v3 path follows only registered index/shard links; a `Path.rglob` canary still succeeds. Legacy compatibility is fixed-path and explicit-link only, private-only, and deprecated. |
| Full / incremental / no-op rebuild equivalence | `implemented` | Existing projection incremental, recovery and v3 integration tests compare manifest/component/page hashes and recovery boundaries. |
| Owner Markdown protection | `implemented` | Existing ownership regression proves a non-manifest owner file is preserved and tampering fails closed. |
| Snapshot/Vault rollback | `implemented` | Existing maintenance tests verify snapshot verification, restore and failed post-swap recovery. |
| Single Knowledge Revision revert | `not_implemented` (outside the current v0.13 contract) | This is a separate operation and is not implemented by snapshot/Vault rollback. |
| Evidence / derived Summary / Agent interpretation separation | `implemented` | Source pages label exact evidence separately from `origin=agent_derived`, `authority=none`, `legal_authority=false` summaries; coverage tests reject treating these page families as interchangeable. |

None of FTS, Dense, Graph, links, centrality, communities, Canvas or usage counts establishes
canonical identity, Authority, legal authority, scope, sensitivity or lifecycle.

## Recent Changes remediation record

The pre-fix public regression was:

```text
tests/test_v013_wiki_recent_changes_parity.py: 3 failed
```

Each path returned a current-object browse rather than the generated event pages. The minimal fix
keeps ordinary reads read-only and does not write the Canonical Ledger. It requires:

```text
verified WikiProjectionBundle
  -> exact Resolver admission for index and each returned shard
  -> Page Registry aggregate/lifecycle/scope/sensitivity admission
  -> bounded registry-declared read_page
  -> UTF-8/frontmatter/schema/hash/size/event_count validation
  -> bounded response contract
```

The response records index content/hash/size, returned shard path/event_count/content hash/size and
stable page/revision identities. Index and shard `audit_head` values are parsed as bounded hashes,
while the runtime bundle binds the active projection to its verified audit heads. `limit` bounds
returned shard descriptors; `history_truncated` comes from the projector's index frontmatter, not
from guessing from current objects. If a shard or retained history is omitted, `truncated` and an
explicit reason are returned. The response always contains `write_performed=false`.

Post-fix evidence:

```text
uv run --frozen pytest -q tests/test_v013_wiki_recent_changes_parity.py
6 passed
```

This proves only the bounded current projection read. It does not prove complete Ledger history,
historical pagination, object diffs, or `as_of` browsing.

## Decision

The local v3 Wiki network and Recent Changes bounded read seams have reproducible development
evidence, with explicit partial and not-executed boundaries above. Large Relation/Wiki runs,
external identity Gold, real hosts, cross-platform runs, legal evidence, Human Gold scoring and
publication artifacts remain required. Therefore this source candidate remains unreleased:

```text
release_gate_passed=false
claim_eligible=false
competitive_claim_eligible=false
```

No claim of perfection, leadership, SOTA, RC, GA, complete validation or competitive superiority is
made.
