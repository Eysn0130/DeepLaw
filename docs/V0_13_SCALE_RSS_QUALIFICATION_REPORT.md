# DeepLaw v0.13 scale, latency, concurrency and RSS qualification

Status: **development diagnostics executed; final clean-commit requalification pending and
release gates unmet** (2026-08-08). All measurements are source-free local construction evidence.

## Frozen environment and boundaries

The executed host is Darwin `25.5.0`, arm64, 12 logical CPUs, Python `3.12.13`, SQLite `3.50.4`,
and 25,769,803,776 reported RAM bytes. Package version remains `0.12.0`. Reports hard-code
`claim_eligible=false`, `competitive_claim_eligible=false` where applicable, and
`release_gate_passed=false`.

No expensive request is silently replaced by a smaller fixture. A requested lane is either
executed at its exact size, fails with the retained reason, or is `not_executed`.

## Query v6 Statement scale

The current public Profile-v3 fixture randomizes source order and stable identities, queries
positions at the beginning, middle, beyond 5,000 and tail, and retains a 512-Statement candidate
bound plus 64 KiB provider hard limit.

| Statements | Positions | Exact targets selected | Candidate count per query | Max provider bytes | Query elapsed |
| ---: | --- | --- | --- | ---: | ---: |
| 5,001 | `0, 2500, 5000` | 3/3 | `1,1,1` | 5,962 | 35,489.395 ms |
| 10,000 | `0, 5000, 5001, 9999` | 4/4 | `1,1,1,1` | 6,026 | 95,361.180 ms |
| 100,000 | `0, 5001, 50000, 99999` | 4/4 | `1,1,1,1` | 6,037 | 1,603.206 ms warm queries + 302,168.088 ms startup verification |

The first 100k observation reproduced the Wiki page-bound failure and is retained as a failure
witness. The post-remediation dirty-tree development run built exactly 100,000 Statements,
completed the derived rebuild, selected all four targets through one persistent verified snapshot,
and observed exactly one full verification. Query time above excludes the separately reported
startup verification. It is not final candidate evidence; a clean implementation-commit rerun must
replace it.

## Living Wiki and construction scale

The corrected current 10k construction fixture recorded 10,000 Assets, 74,518,528 SQLite bytes,
300 files, 5 bounded aggregate Canvas files and a 7,605-byte provider payload with zero hard-limit
violations. `compiled_first` measured 9.637 ms against the frozen 1,000 ms target; `context`
measured 10.042 ms against 1,200 ms; startup verification measured 15.738 ms and was not repeated
per request; eight readers succeeded in 1.450 s. Incremental and full rebuild measured 2.121 s and
1.798 s. These are dirty-tree development observations pending clean candidate binding.

The first 100k construction attempt failed before qualification because the benchmark produced
300,000 lines and crossed the Source line-count ceiling. The runner now generates exactly 200,000
lines (one heading and one body line per Asset) and has a regression for that exact count. A second
benchmark defect unconditionally labelled every full rebuild as a whole-Vault filesystem scan;
the runner now observes `Path.rglob` and distinguishes the Vault root from projector-owned
staging/backup subtrees.

The post-remediation dirty-tree 100k development run executed the exact workload: 100,000 Assets,
739,852,288 SQLite bytes, 1,945 files, 5 Canvas files and a 7,605-byte provider projection.
`compiled_first`/`context` measured 25.894/25.945 ms, startup verification 33.580 ms with
`per_request_full_verify=false`, incremental/full rebuild 19.118/16.445 s, and eight readers
24.614 s. Full rebuild observed no whole-Vault scan. Wiki page/backlinks/outlinks measured about
6.4–6.7 s; no 100k threshold was frozen for those operations, so no pass/fail is invented. The
pre-fix artifact remains a reproduction and the current figures remain development observations
until the clean implementation-commit report is generated.

## 10,000-request RSS and eight readers

The dedicated child-process runtime diagnostic uses one real `knowledge_support` MCP lifespan for
10,000 Query v6 reads, samples current RSS using macOS `ps`, and checks Canonical Ledger event
counts before/after. Its executed pre-final observation was:

| Measurement | Observation |
| --- | ---: |
| Attempted / successful / failed requests | 10,000 / 10,000 / 0 |
| Start / end RSS | 81,297,408 / 88,260,608 bytes |
| Relative RSS growth | 8.565095% |
| Frozen growth limit | 10.0% |
| Independent readers | 8/8 |
| Ledger events before / after | 9 / 9 |

The runner records the 10% threshold and an explicit boolean judgment, binds Query v6, Persistent
Runtime and MCP server source hashes, and must be rerun against the clean implementation commit.
The measurement is current RSS before/after, not peak RSS; that limitation remains explicit.

## Not executed / not claimed

- 10k/100k governed Relations and the 500/5,000 Relation truncation boundary: `not_executed`.
- Source-update timing in the construction runner: `not_executed`; functional first-read cache
  invalidation is tested separately and not relabelled as timing evidence.
- Linux and Windows: `not_executed`; local Darwin Python-version evidence is reported separately
  and cannot satisfy the 3-OS matrix.
- Real-user latency/RSS, model token use, final blind holdout and competitive comparison:
  `not_executed`.
- Exact signed 28-source Pack, Human Gold, real Codex, OpenCode/DeepSeek and public artifact
  redownload: `not_executed` or `review_pending`.

Therefore the local observations do not authorize a scale-complete, production-readiness, RC, GA,
quality or superiority claim. `scale_gate_passed=false` until clean-commit post-fix reports are
generated and every mandatory external gate is separately satisfied.
