# DeepLaw v0.13 Living Wiki and knowledge-network qualification

Status: **local deterministic network regressions and clean 1k/10k/100k construction passed;
external quality gates remain separate** (2026-08-08). This is development evidence, not RC/GA or a
competitive claim.

## Executed network chain

The local qualification exercises the governed chain rather than treating Markdown links as
Authority:

```text
Source Revision / Fragment / Locator
-> Knowledge Revision / Statement / Relation
-> current pointer and immutable history
-> rebuildable FTS / Dense / Graph
-> Page Registry / Link Index / Resolver
-> Wiki pages / backlinks / outlinks
-> Query Plan v6 / bounded Capsule
```

The focused Wiki suite covers all 12 Knowledge kinds (`claim`, `concept`, `entity`, `event`,
`decision`, `procedure`, `experience`, `preference`, `synthesis`, `comparison`, `skill`, and
`memory`) through public owner-granted mutation seams. It then performs full, no-op, incremental,
and final no-op rebuilds; compares exact manifest/component/page hashes; verifies stable page IDs
and paths across a new revision; reads backlinks/outlinks through the Link Index; resolves stable
Knowledge identities; and proves an owner Markdown file outside the generated manifest is
unchanged.

The current projector also keeps large Statement Evidence collections bounded. Revisions with
more than 64 Statements produce deterministic shard pages of at most 64 Statements. Every shard
is in the v2 manifest and v3 Page Registry, every Statement keeps its stable anchor and receipt,
and the canonical Knowledge page links all shards. This remediates the reproduced 100k failure in
which 1,000 inline Statements exceeded the 256 KiB Wiki page/read boundary.

## Local executable evidence

The consolidated Wiki command is:

```bash
uv run --frozen pytest -q \
  tests/test_living_wiki_delivery.py \
  tests/test_living_wiki_quality.py \
  tests/test_v013_persistent_wiki_source.py \
  tests/test_v013_wiki_coverage_spec.py \
  tests/test_v013_wiki_link_index.py \
  tests/test_v013_wiki_network_qualification.py \
  tests/test_v013_wiki_page_families.py \
  tests/test_v013_wiki_registry.py \
  tests/test_v013_wiki_resolver.py
```

Result: **68 passed, 3 explicitly skipped**. The skips are `wrong_merge`, `alias_collision`, and a
second Wiki-specific cycle construction. They are not counted as pass. Cycle, contradiction,
temporal, dangling-endpoint and self-loop behavior is exercised separately through the governed
Relation fixture; same-name identity, source successor, withdrawal/forget, rename/move and
selective invalidation are covered by their canonical subsystem regressions and are not relabelled
as one synthetic Wiki test.

## Coverage disposition

| Area | Status | Evidence boundary |
| --- | --- | --- |
| 12 Knowledge kinds | `pass` (development) | Public sink mutation, Registry, Resolver and kind browse. |
| Stable identity across revision / rename / move | `pass` (local regressions) | Page ID/path stability plus canonical reconcile/source lifecycle tests. |
| Owner-file preservation | `pass` | Non-manifest Markdown bytes remain identical across rebuilds. |
| Full/no-op/incremental hash equivalence | `pass` | Exact v2/v3 manifest, Registry, Link, Resolver and page hashes. |
| Backlinks/outlinks/Resolver | `pass` | Indexed reads report `index_used=true`; no Wiki filesystem-search substitute. |
| Profile switch/sharding/no per-object Canvas | `pass` locally | Profile regressions plus bounded kind/source/Statement shards; scale evidence is reported separately. |
| Graph cycle/contradiction/temporal/dangling/self-loop | `pass` at smoke scale | Governed Relation fixture only. |
| Wrong merge / duplicate identity / cross-language alias quality | `not_executed` as a Wiki qualification metric | Existing negative identity tests exist, but no frozen external identity Gold was supplied. |
| 500/5,000 Relation truncation and 10k/100k Relations | `not_executed` | No safe audited bulk Relation constructor; the 120 mutations/minute owner grant was not weakened. |
| 100k derived Wiki rebuild after Statement sharding | `pass` as clean-commit development evidence | Exact 100,000-Statement and 100,000-Asset lanes completed derived/full/incremental rebuilds at implementation commit `bb6a942970186f03ea41e108a2eceaaca54e3bcb`; report hashes are bound in `V0_13_SCALE_RSS_QUALIFICATION_REPORT.md`. |

FTS, Dense, Graph, Wiki and Canvas remain derived and rebuildable. None of their scores, links,
centrality, communities or usage counts alter canonical origin, verification, Authority, scope,
sensitivity or lifecycle.

## Decision

The deterministic Wiki network is materially covered and both reproduced scale blockers—the
unbounded Statement page and quadratic Source Summary aggregation—have minimal
derived-projection fixes. Clean-commit 100k Statement and Asset runs completed derived rebuilds,
avoided a whole-Vault filesystem scan and kept five aggregate Canvas files. External identity Gold
and large Relation lanes are still mandatory for their respective claims. Until those gates
execute, `wiki_release_gate_passed=false`, `release_gate_passed=false`, and
`competitive_claim_eligible=false`.
