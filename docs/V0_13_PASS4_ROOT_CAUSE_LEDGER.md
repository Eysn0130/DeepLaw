# DeepLaw v0.13 Pass 4 root-cause ledger

Status: **active current-source remediation ledger; not release evidence** (2026-08-09).

This ledger is bound to the Pass 4 starting candidate and to the retained GitHub raw evidence
below. It is repository-visible development material. It is not Human Gold, a blind holdout, a
Core Gate result, or permission to release.

## Frozen starting candidate

```text
branch=codex/v013-evidence-provenance
commit=403dd0a3d8dbe8d163ea5f66d6a149dda388196c
tree=8f93885707b897a2a3f9df8e1b9181f91310a587
package_version=0.12.0
pull_request=22
tag_at_head=absent
release_gate_passed=false
claim_eligible=false
competitive_claim_eligible=false
```

## Frozen GitHub failure evidence

Evidence was downloaded through the GitHub Actions API at `2026-08-09T14:11:35Z`. The downloaded
files are kept outside the repository. GitHub artifact digests bind the uploaded archives; the
file hashes below bind the extracted reports and downloaded run-log archives used for diagnosis.

### Semantic Living Wiki evidence — run `31304630903`

```text
candidate_commit=403dd0a3d8dbe8d163ea5f66d6a149dda388196c
workflow_conclusion=failure
artifact_id=9035691379
artifact_name=semantic-deterministic-review-candidate
artifact_archive_sha256=75bf7394bbbe7d8d96a2733342b2dd4a929e3b52f8f94e64b8270c796bc3baf1
downloaded_run_logs_zip_sha256=3a437b7d3ac7846ea6d288d4e9e9ffff9405332d99926fb4a53afbec5127a5ed
deterministic_semantic_lifecycle_v2_sha256=06700ca141802bc1a47d2f2b576293da625b85acc3e7e76fca4329ec858afd60
semantic_query_report_sha256=00312cf5e1b2e8eb92dd2e6cbe079a62d0175e4d190644e89809e361693f97cd
semantic_query_cost_sha256=ef014d1b7b036e2cdceae5463e9105e58fb4b318a6cba3b05ae8bf117ce7d2c2
```

### Commercial GA gates — run `31304630881`

```text
candidate_commit=403dd0a3d8dbe8d163ea5f66d6a149dda388196c
workflow_conclusion=failure
downloaded_run_logs_zip_sha256=230fbd1b9a643489820fd0cfbf779a8bf460e6e92aa117afd7b41d3957942f35
verified_dist_artifact_id=9035550787
verified_dist_archive_sha256=1f056d1a20c6e4d90db8b35a6501c8eccf825e9b50eae50decca7cb86f892001
verified_oci_artifact_id=9035562469
verified_oci_archive_sha256=1dadc1930ef8d8267b9d6d5bf3176376452dc464643c090d5968a5f3d1cb1671
no_model_host_artifact_id=9035563918
no_model_host_archive_sha256=f2ffdd9377bf27e7a7b197c1a4555f827158d8ab807956a14d2ed607dfb99e2c
living_wiki_artifact_id=9035868023
living_wiki_archive_sha256=ffb9d78291d178251cefc22e4a3a45935888b5f32bf3693e269491498bd1efec
living_wiki_baseline_report_sha256=07935bb768a19ab18dc465d99e94f3b6694543ede881de437eb8dbc8f4e32f96
living_wiki_candidate_report_sha256=a7d4d965bc9039027a8a495872dc847a9f2e86fec6638511704ae4ccb18289af
wheel_sha256=7c9abdb2ca704986e0aab00a15fd418e4c6ee5b3ebedfe3675e04b9b964ab719
sdist_sha256=c708475e3db78dea995381644f1cf8f4e1ae7d537ad3fb516009bcf60666cfec
```

## Root-cause records

Each record must retain the observed failure. A repair may change only the root cause proven by a
public-seam reproduction; it must not edit the frozen reports, Gold, expected identities, or
thresholds above.

| ID | Reproduction | Root cause | Affected public seam | Authority / security / privacy impact | Minimum repair | Regression | Requalification status | Blocked / not executed |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| `P4-CTX-010` | Frozen case 10 and a public Python/CLI/MCP regression: withdrawing Policy A allowed another current policy to satisfy a named freshness request | Generic target matching enforced freshness but not the single policy designator; the proposed “lost raw CJK anchors” common cause was disproved by a 0/3 black-box retention-only ablation | Python/CLI/MCP Context v6 | A withdrawn target could be replaced by unrelated current content instead of a Gap | For a single named-policy freshness request, admit only the matching designator; retain normal Authority, lifecycle and budget admission | `tests/test_v013_query_v6_multilingual_context.py` plus exact-wheel semantic development suite | Local development requalification passed: case 10 returns a bounded Gap and `stale_prohibited_selections=0` | External Gold and real Host not executed |
| `P4-CTX-011` | Frozen case 11 and public Context regression omitted the required revision-bound comparison Synthesis | The default generic expansion profile lacked bounded atomic Chinese concepts for diagnostic/retention semantics; expansion did not supply enough cross-language overlap for governed compiled selection | Python/CLI/MCP Context v6 | Conflict/duty context could be silently incomplete | Add only generic atomic aliases and a digest-bound expansion receipt; do not change stored evidence, identity, Authority or limits | `tests/test_v013_query_v6_multilingual_context.py`; `tests/test_v013_query_expansion_governance.py` | Local development requalification passed: two policies, comparison Synthesis and contradiction retained without unrelated Source | External Gold and real Host not executed |
| `P4-CTX-014` | Frozen case 14 and public Context regression selected a generic quote instead of the verification-badge claim/Quote/Locator | The default generic expansion profile lacked bounded atomic verification/badge/exact/color concepts | Python/CLI/MCP Context v6 | Exact-evidence duty could be replaced by a generic quote candidate | Add generic atomic aliases and expose profile, term digest and truncation in Query Plan v6 | `tests/test_v013_query_v6_multilingual_context.py`; Query Plan schema parity tests | Local development requalification passed: exact claim and minimum Quote/Locator selected | External Gold and real Host not executed |
| `P4-WIN-ENV` | Frozen Windows run failed importing `_overlapped` under a hand-built environment | Test launchers omitted explicit Windows process bootstrap variables | CLI Context tests and closed Host launch helpers | An incomplete allowlist broke the public seam; ambient inheritance would leak secrets | Reuse one closed allowlist containing only portable process variables and explicit isolated `HOME`/`PYTHONPATH` overrides | `tests/test_subprocess_environment.py`; CLI route regressions; ambient/provider canary tests | Local closed-environment tests passed | Windows 3.11/3.12/3.13 current-head results pending CI |
| `P4-WIN-ATOMIC` | Frozen Windows Claude settings write failed around `fchmod`/temporary replacement | Atomic writer assumed POSIX descriptor modes and cleanup could mask the primary exception | Claude settings lifecycle helper | A failed cleanup could hide root cause; POSIX mode was not a Windows ACL guarantee | Close descriptor on every path, flush/fsync then replace, apply 0600 only on POSIX, and report no Windows owner-only guarantee | `tests/test_v013_claude_lifecycle.py` | Local fault-injection and lifecycle regressions passed | Native Windows execution pending CI; native ACL remains a separate gate |
| `P4-WIN-SOURCE` | Frozen Evidence Wiki exact Source-byte assertion failed on Windows | Text-mode newline translation changed immutable Source Revision bytes | Source ingestion in the Evidence Wiki benchmark | Source hash/Quote/Locator identity could diverge by platform | Write the frozen UTF-8 byte sequence directly | Evidence Wiki candidate and Source-byte contract tests | Local exact-byte regression passed | Native Windows execution pending CI; exact Legal Pack not executed |
| `P4-WIN-FS` | Frozen scale report called unavailable `os.statvfs` on Windows | Filesystem metadata probe assumed POSIX APIs | Scale raw-report runner | Fabricated POSIX metadata would make provenance misleading | Return `kind=unknown` and null block size when the portable probe is unavailable | `tests/test_v013_scale_performance.py` | Local absence/`NotImplementedError` regressions passed | Native Windows scale qualification not executed |
| `P4-WIN-REDACTION` | Frozen fresh-wheel diagnostic retained Windows path separators after redaction | Redaction normalized only one separator form and too late | Fresh-wheel verification report | Private-path disclosure checks were platform-dependent | Redact both absolute path syntaxes, then normalize separators | `tests/test_verify_fresh_wheel.py` | Local POSIX/Windows-form regression passed | Native Windows fresh-wheel lane pending CI |
| `P4-WIN-STABILITY` | Frozen Windows runtime-stability report observed `statement_count=0` with only an exception class | Failure reporting discarded the bounded pipeline stage | Runtime-stability raw report | An invalid run was not diagnosable and must not be promoted to pass | Emit only `fixture_failure:<closed_stage>:<closed_error_type>` and require the same reason for unexecuted lanes | `tests/test_v013_runtime_stability.py` | Local redaction/closed-enum tests passed | Underlying Windows cause intentionally not guessed; current-head raw report pending CI |
| `P4-GATE-TOPOLOGY` | Run `31304630881`: Platform gate treated six explicit scale/Wiki skips as platform failures | Platform and qualification inventories were conflated | Candidate CI and commercial Platform Core workflow | Simply accepting all skips would weaken Core gates | Freeze exact common/Windows inventories; exclude qualification markers from Platform Core; require historical v0.6 fixture; retain qualification as `not_executed`; bind uv and manifest in a closed receipt | `tests/test_v013_platform_core_manifest.py`; `tests/test_commercial_release.py` | Local manifest/invariant/negative tests passed; candidate CI now schedules 3 OS × 3 Python without release eligibility | Real nine-row Platform Core evidence not executed until current-head CI/manual Core workflow completes |
| `P4-WIKI-PERF` | Frozen run plus two alternating same-runner repetitions reproduced cold/warm/incremental/rebuild regressions | A redundant read-plane integrity verification was one proven open-time hotspot; removing it safely did not close the dominant projection/rebuild regression | `KnowledgeOS.open` and Living Wiki comparison | Performance affects human/Agent latency but not Authority | Reuse the already-open legacy snapshot for integrity verification only; persist schema-bound failed comparison receipts; do not add freshness-bypassing caches | `tests/test_v013_python_read_runtime.py`; `tests/test_living_wiki_quality.py` | Still failed: both repetitions exceeded frozen performance limits, especially rebuild | Core performance blocker remains; no speculative optimization accepted |
| `P4-RAW-SCALE` | Audit found current scale runners partially manufacture semantic inventory through private SQL/Ledger helpers | Existing fixture construction is not fully through the public coordinator/CLI/MCP seam | Statement/Relation/Graph/Wiki/RSS/concurrency/cache qualification | Private writes could bypass governance and fabricate qualification | Record `qualification_fixture_blocked`; do not run the existing helpers as Core evidence; defer any evaluator-only fixture contract to a separate Sol decision | Existing skipped cases remain explicitly marked `qualification` | `not_executed` / `qualification_fixture_blocked` | 10k/100k, Relation/Graph, Wiki identity, RSS, concurrency and cache Core lanes remain blockers |
| `P4-PROVENANCE` | Twelve Core raw validators and the selective-forget raw contract remain absent | Deliberate fail-closed readiness gap, not a product feature defect | v0.13 evidence assembly | Enabling assembly would permit unproven release evidence | Keep historical/additive classifications fail closed and `assembly_enabled=false` | Existing negative assembly tests | `blocked_missing_validator` / `blocked_missing_raw_contract` | Human Gold, Legal Pack, real Host and trusted attestation inputs absent |

## Local development evidence

The following results are diagnostic and regression evidence only. They do not satisfy an external
Core or Human-Gold gate.

```text
semantic_candidate_wheel_sha256=d3123c9886a0e9565a73406e8382b8ab98768e6d8f396b322dbc64db5281aaed
semantic_candidate_sdist_sha256=8b7e11ea2d7fe6e4201bdf66db7c4ddb908a28f782922303d5f63534b3fc1d26
semantic_corpus_sha256=59e638dfc802e6036525afaa1de28f425e12ed3a4d63975e6f9b4cc528de726d
semantic_lifecycle_sha256=2430298ac20ac9da9b3e76b0ec31d18da799bc2bf1184d1e3a0faba580f8ab82
semantic_query_report_sha256=fb9eba6a7e407ad71a53dc80f318ecd303ab0c3da861ee8ddd11deafc5565fb9
semantic_query_cost_sha256=63c735819c81ca81f3c5399c461da89a2cc7c56aa156b4bfc5a66090a95e8140
semantic_canonical_cases=15/15
semantic_query_variants=14/14
context_semantic_accuracy=1.0
stale_prohibited_selections=0
provider_hard_limit_violations=0
platform_core_manifest_sha256=abd08efc2b61a75d3be9f9f131ffbebacdc0fd008dd7d4a0d82e0ab0cd7491bf
```

Alternating performance diagnostics used baseline order `B1,C1,B2,C2` on one macOS/Python 3.12
runner. Both comparison commands wrote a closed failed receipt and exited non-zero:

| Repetition | Cold p50 | Warm p50 | First compilation | Incremental | Rebuild | Result |
| --- | ---: | ---: | ---: | ---: | ---: | --- |
| 1 | `+25.1%` | `+25.0%` | `+10.9%` | `+34.4%` | `+113.4%` | `failed` |
| 2 | `+32.0%` | `+31.0%` | `+24.5%` | `+30.7%` | `+116.1%` | `failed` |

## Fixed release boundary

```text
package_version=0.12.0
release_gate_passed=false
claim_eligible=false
competitive_claim_eligible=false
final_release_disposition=source_candidate_remains_not_released
```
