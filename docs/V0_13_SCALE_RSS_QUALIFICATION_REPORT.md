# DeepLaw v0.13 scale, latency, concurrency and RSS qualification

Status: **clean implementation-commit development diagnostics executed; external release gates
remain unmet** (2026-08-08). These measurements are source-free synthetic construction evidence,
not real-user quality or release evidence.

## Candidate and report bindings

All committed final reports bind clean implementation commit
`bb6a942970186f03ea41e108a2eceaaca54e3bcb` and tree
`8817db9349b504784b95690844ee10f43769cbdd`. The host was Darwin `25.5.0`, arm64,
Python `3.12.13`, SQLite `3.50.4`; package version remained `0.12.0`.

| Report | File SHA-256 | Internal report SHA-256 |
| --- | --- | --- |
| `benchmarks/v013/query-graph-scale-final-5k-10k-2026-08-08.json` | `ad16d230360610e40037808ad9efdd75ccd5b8b02eda7f51bec15c0a753c185a` | `ea2d235907613979f97fb7b91e4b4377e963e0132c9a940441b2dbd4437f147d` |
| `benchmarks/v013/query-graph-scale-final-100k-2026-08-08.json` | `ec362bb5d57c4b702668d0a5f4098996ad8f88746f455e80a47393fb3cb6b1eb` | `3f632601de455a439106cc9f1be0a59ada58c90d496b0abfe5c520e01f3ab4e9` |
| `benchmarks/v013/scale-performance-final-2026-08-08.json` | `e905c2c228b78abcdb917018316d2b07adc8c708050da0e7c3bda9a1eb36830a` | `e0d15a421678eaa4f12bae7f1923233432aed948a766cb232b8a70a6fe0f59b7` |
| `benchmarks/v013/runtime-stability-final-10000-2026-08-08.json` | `0430b3fb31a7377d7d48b1525aa48972ec4e70ed839679b2fc54e1f46799268e` | `9d168fe849fbaf8d33b1ad7dd62850049365a3fabf70d650329db7066a796d72` |

The runners make an expensive requested lane either execute at its exact size or record
`not_executed`; they never substitute a smaller fixture.

The Context default-drift continuation created two additional clean-worktree Query reports bound
to implementation commit `ee06bb3ef9989c671638deda95968690d628f8ca` (tree
`5fe2895a7c50f496a23612969844cd390b3cafad`). They re-executed Statement tail recall after the
Context v6 fix; they did not rerun the construction/RSS suites above:

| Post-remediation report | File SHA-256 | Internal report SHA-256 |
| --- | --- | --- |
| `benchmarks/v013/query-graph-scale-context-v6-5k-10k-2026-08-08.json` | `70f5d551a4bdcc9cbcf1a2210652577068afa9bf8168eae40b002757b2c3e424` | `224093a14cea7bf57f1e1013f243d3eeb2efa9638c0c1db65adcb27c72b82332` |
| `benchmarks/v013/query-graph-scale-context-v6-100k-2026-08-08.json` | `e69d2f6eb7115db45a56137d224a2320b3f7633b06cae86185fe9248fa3bca5f` | `0e07c85741446fd658d754b4acea08ed5f73966db5b3d25bf0f1101695188a41` |

The 5,001/10,000/100,000 Statement sub-lanes selected every exact target with one candidate and
maximum Provider content of 6,037 bytes. Derived rebuilds executed and the reports recorded
`working_tree_dirty=false`. The governed Relation/truncation sub-lanes remained `not_executed`, so
the report's aggregate scale-lane status remains fail-closed rather than being promoted to pass.

## Query v6 Statement scale

The Profile-v3 fixture randomizes order and stable identities, targets the beginning, middle,
position 5,001 and tail, retains a 512-Statement candidate bound and enforces the 64 KiB provider
limit. One timed `PersistentReadRuntime` verification is reused by every warm query.

| Statements | Positions | Selected | Candidates/query | Max provider bytes | Startup verify | Warm queries | Build |
| ---: | --- | ---: | --- | ---: | ---: | ---: | ---: |
| 5,001 | `0,2500,5000` | 3/3 | `1,1,1` | 5,963 | 11,946.590 ms | 131.598 ms | 21,461.240 ms |
| 10,000 | `0,5000,5001,9999` | 4/4 | `1,1,1,1` | 6,029 | 23,912.870 ms | 269.383 ms | 43,950.764 ms |
| 100,000 | `0,5001,50000,99999` | 4/4 | `1,1,1,1` | 6,037 | 320,286.464 ms | 1,830.675 ms | 567,038.207 ms |

All three exact lanes completed the derived rebuild, observed one full verification, recorded
`per_request_full_verify=false`, and selected every exact target independent of position. The 100k
fixture used 1,116,682,853 bytes and 302,486 files; process peak RSS observed by the runner rose
from 62,685,184 to 1,866,104,832 bytes during construction. These are host-local construction
measurements, not a production capacity promise.

## Living Wiki and construction scale

The 60-operation scale report contains 47 executed observations, 15 frozen-threshold passes,
zero failures, zero degraded outcomes and 13 explicit `not_executed` outcomes.

| Assets | compiled/context p95 | Wiki/backlinks/outlinks p95 | Incremental/full rebuild p95 | 8 readers | SQLite / files / Canvas | Provider bytes |
| ---: | --- | --- | --- | ---: | --- | ---: |
| 1,000 | 7.297 / 9.023 ms | 61.410 / 76.523 / 75.799 ms | 347.178 / 274.185 ms | 399.274 ms | 8,400,896 / 97 / 5 | 7,665 |
| 10,000 | 9.556 / 10.322 ms | 547.061 / 522.522 / 542.241 ms | 2,174.390 / 1,797.805 ms | 1,600.195 ms | 74,498,048 / 306 / 5 | 7,665 |
| 100,000 | 26.604 / 26.455 ms | 6,460.966 / 6,245.596 / 6,409.959 ms | 18,826.020 / 16,229.254 ms | 25,977.414 ms | 740,503,552 / 1,951 / 5 | 7,665 |

Every full rebuild observed `full_filesystem_scan=false`, every verified warm path recorded
`per_request_full_verify=false`, and provider hard-limit violations were zero. The five Canvas
files are bounded aggregate views rather than one file per object. Wiki/backlink/outlink operations
at 10k/100k have no frozen latency threshold, so their execution is not relabelled as a pass.

Source-update timing and the corresponding scale-runner cache invalidation observation were
`not_executed` because the construction fixture does not fabricate a canonical source mutation.
Functional first-read invalidation was independently exercised by 18 persistent-runtime/cache
regressions.

## 10,000-request RSS and eight readers

The dedicated child process kept one real `knowledge_support` MCP lifespan open for all requests.

| Measurement | Clean candidate observation |
| --- | ---: |
| Attempted / successful / failed | 10,000 / 10,000 / 0 |
| Start / end current RSS | 81,264,640 / 88,260,608 bytes |
| Current RSS growth | 8.608871% |
| Frozen growth limit | 10.0% |
| Independent readers | 8/8 |
| Canonical Ledger events before / after | 9 / 9 |

The frozen current-RSS criterion passed on this Darwin host. The report explicitly does not claim
peak-RSS or cross-platform stability.

## Not executed / not claimed

- 10k/100k governed Relations and the 500/5,000 Relation truncation-position gate:
  `not_executed`; the owner grant limit of 120 mutations/minute was not weakened.
- Linux and Windows scale/RSS evidence: `not_executed`.
- Real-user corpus latency/RSS, final blind quality, exact signed legal Pack, Human Gold, real
  Codex, OpenCode/DeepSeek and competitive comparators: `not_executed`.

Therefore `scale_gate_passed=false`, `release_gate_passed=false`, and
`competitive_claim_eligible=false` despite the completed local synthetic lanes.
