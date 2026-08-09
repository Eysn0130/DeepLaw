# DeepLaw v0.13 Pass 4 disposition

Status: **current-source remediation completed locally; release qualification remains open**
(2026-08-09).

This document records a current fix and qualification-infrastructure pass. It is not a release
manifest, Human Gold, a blind holdout, a Core Gate result, or permission to publish.

## Disposition

```text
final_release_disposition=source_candidate_remains_not_released
package_version=0.12.0
release_gate_passed=false
claim_eligible=false
competitive_claim_eligible=false
tag_created=false
published=false
```

The exact implementation candidate reviewed in this disposition is:

```text
branch=codex/v013-evidence-provenance
commit=26030fa46249f3ca47fd028bce985e0cdbac1b67
tree=c488e0ce403f650f4c5dae470f01a422aec1430b
pull_request=22 (Draft)
package_version=0.12.0
```

The documentation commit containing this disposition is not substituted for that implementation
binding. Current-head GitHub checks must bind their own exact commit and tree after push.

## Contract and protocol binding

```text
prd_1_2_sha256=daa524d62471801ca79699948ebca52ab194e14adcdf0bc1d332850fd7a12fb8
qualification_protocol_sha256=824a5d63939776da09e4e403836b198b05f8a70e0b6b778f145acd3b6b7c8442
prd_traceability_sha256=9fa12271555898b85583b710044c9ad1aa585486458f636ef5738fcc8980acbb
query_plan_v6_schema_sha256=356dba01456ed89c2938331cc2335fc26913d67d40e851d63146983c40922969
platform_manifest_file_sha256=f28fbb21908099f7341e66939427a3d7a8d90ff427c0316d6d98accad4e0d951
platform_manifest_internal_sha256=abd08efc2b61a75d3be9f9f131ffbebacdc0fd008dd7d4a0d82e0ab0cd7491bf
platform_manifest_schema_sha256=e65dbe0ea8fa98d383b19be42400c7be733de1d0c748cc00a44f03584896a94e
platform_binding_schema_sha256=7ddd3e87149b7cbafda4f978959ba5a2589e43c46ead01151717905664e551ca
comparison_failure_schema_sha256=df48242e107cd29476f94c46f4be9f36a0987d08e5d14022c309ebe70942e27b
v013_classification_v2_sha256=4efbb8096f0fc57fbb8cc1ffe76e794e3bc6022b0969d1d980dfc80c112a90e2
v013_classification_v2_schema_sha256=050ab23c714e65e8ffd0121de975c012e1ea4ff148f294c47f77f900c0c67ef9
assembly_enabled=false
```

## Repairs accepted by Sol

### Query v6 Context

The proposed common root cause—expansion replacing the original CJK lexical anchors—was not
reproduced. A black-box retention-only ablation fixed none of cases 10, 11 or 14. The accepted
minimal repairs instead address two proven causes:

- a single named-policy freshness request now fails closed when a candidate has a missing or
  different policy designator; withdrawal no longer permits Policy B or a comparison Synthesis to
  stand in for withdrawn Policy A;
- default deterministic expansion gains only bounded, generic atomic concepts needed for
  diagnostic/retention and verification/badge duties; it contains no expected identity, case
  phrase, or benchmark-specific answer;
- Query Plan v6 records the expansion profile, term count, term digest, truncation, and two fixed
  false assertions showing that expansion changed neither Authority nor stored evidence.

Python, CLI and MCP public seams are covered. The 20-revision discovery limit, 512-Statement
candidate limit, 64 KiB Provider hard limit, and all Authority/scope/sensitivity/lifecycle/temporal
admission remain unchanged.

The repository-visible development suite, run from an exact pre-disposition wheel, reported:

```text
canonical_cases=15/15
query_variants=14/14
query_variant_pass_rate=1.0
context_semantic_accuracy=1.0
stale_prohibited_selections=0
provider_hard_limit_violations=0
citation_validity=1.0
claim_evidence_binding=1.0
```

This corpus was visible during remediation and is therefore development material, not Human Gold
or a qualification/final-blind holdout.

### Windows and closed-process behavior

Accepted fixes:

- a shared closed subprocess environment inherits only portable bootstrap variables, including
  explicit Windows `SYSTEMROOT`, `WINDIR`, `COMSPEC` and `PATHEXT` when present; ambient and
  Provider secrets remain absent;
- Claude settings writes close every descriptor, flush and `fsync` before `os.replace`, preserve
  the primary exception during cleanup, and claim POSIX 0600 only on POSIX;
- Evidence Wiki development Sources use exact UTF-8 bytes;
- unavailable filesystem probes return honest `unknown`/null metadata;
- fresh-wheel diagnostics redact both path syntaxes and normalize separators;
- runtime-stability fixture failures expose only a closed stage and closed exception category;
- the runtime-stability RSS child now uses the same closed environment with a caller-owned isolated
  home, including Windows `USERPROFILE`, instead of a second three-variable launcher; unsupported
  Windows current-RSS measurement remains honestly `not_executed` while fixture and reader checks
  still execute.

These changes passed local fault-injection and canary regressions. Native Windows 3.11/3.12/3.13
execution is not replaced by the local macOS result. The current-source CI matrix now schedules all
nine OS/Python cells, but its future green result will remain claim-ineligible development evidence.
The manual Platform Core workflow still requires the frozen inventory and exact historical v0.6
fixture.

### Gate topology

Candidate CI and release qualification are now distinct:

- Candidate CI runs current-source regression on Linux/macOS/Windows and Python 3.11/3.12/3.13,
  emits a claim-ineligible receipt, and never invokes `--require-eligible`. Receipt generation now
  uses the exact uv-managed test interpreter and fails closed on a matrix major/minor mismatch.
- Platform Core excludes the six explicitly marked scale/Wiki qualification cases, requires an
  exact JUnit inventory with zero failure/error/unclassified skip, executes native Windows cases on
  Windows, and requires the frozen historical v0.6 wheel.
- Scale/Wiki cases remain `not_executed`; moving them out of Platform Core did not make them pass.
- Commercial workflow retains `workflow_call`, `workflow_dispatch`, exact ref, external evidence,
  and `--require-eligible`. Its pull-request trigger was removed only after Candidate CI existed.
- Release workflow uv is pinned to `0.11.5`; the binding receipt records actual uv version and
  executable digest.

The v0.13 classification remains fail closed. No Core assembler was enabled.

## Local verification

Environment:

```text
OS=macOS 26.5.2 (25F84), arm64
Python=CPython 3.11.15
uv=0.11.5 (95eaa68c8 2026-04-08 aarch64-apple-darwin)
cwd=<repository-root>
```

Commands and outcomes:

```text
uv lock --check
  passed

uv run --frozen pytest --strict-markers -rs
  1338 passed, 9 skipped in 343.10s

uv run --frozen ruff check .
  passed

git diff --check
  passed before implementation commit

uv run --frozen python -m benchmarks.verify_fresh_wheel --dist <verified-dist>
  valid=true

uv run --frozen python -m benchmarks.release.verify_reproducible_build \
  --artifact-dir <verified-dist> --output <reproducible-build-report>
  reproducible=true; artifact_release_eligible=true
```

The last `artifact_release_eligible=true` is strictly the supply-chain report's package-artifact
subgate. It does not set product `release_gate_passed` or permit publishing.

Verified implementation artifacts:

```text
wheel=deeplaw-0.12.0-py3-none-any.whl
wheel_sha256=4cd846432e5efa9bc68ab98cad5d5ba376a9a1d20a6484b01734fe5fd77cf6e8
sdist=deeplaw-0.12.0.tar.gz
sdist_sha256=d36229dab2d060b636677f223aaee817307703d6173f97a29407c43a6ca1c80f
reproducible_build_record_sha256=82fe3f0d0d2be3e506537690ce18555cf5298befe3b73e2114c427cc43b7f341
reproducible_build_report_file_sha256=ed1e0dde0b1944b7088767981099822bf6b136bdd0fd0d1ea49f118be7e8cd42
fresh_wheel_report_file_sha256=7e740fbee7228f9d3473fcc3300803e2bbdbf1ca8ac94d6ac995ad1be54090ca
```

## Skip disposition

| Count | Classification | Meaning | Release effect |
| ---: | --- | --- | --- |
| 3 | required scale/Relation qualification | 10k Statement, 100k Statement, Relation 500/5000 | Core blocker, `not_executed` |
| 3 | required Wiki qualification | wrong merge, alias collision, cycle | Core blocker, `qualification_fixture_blocked` |
| 1 | historical compatibility | exact v0.6 wheel absent from ordinary local pytest | Required in Platform Core; local ordinary suite is `not_executed` for this case |
| 2 | platform-native | Windows ACL and junction | `not_applicable` on macOS; must execute on Windows |

No skip is treated as a pass.

## Living Wiki performance

Two alternating, same-runner development repetitions (`B1,C1,B2,C2`) reproduced the blocker. The
baseline wheel SHA-256 was
`9bda60831e4380092c9a3bdb80103b5ec8abbf5a2be0adf6ffd57f61cfa46ca0`; the diagnostic candidate
wheel SHA-256 was
`ba35f6cb99cbb4d809e2d53839fe8e43dbccfd645ec859bdbfca587c88ad9ab3`.

| Repetition | Cold p50 | Warm p50 | First compilation | Incremental | Rebuild | Comparator |
| --- | ---: | ---: | ---: | ---: | ---: | --- |
| 1 | `+25.1%` | `+25.0%` | `+10.9%` | `+34.4%` | `+113.4%` | failed/non-zero |
| 2 | `+32.0%` | `+31.0%` | `+24.5%` | `+30.7%` | `+116.1%` | failed/non-zero |

The minimal removal of one redundant open-time integrity verification was retained because broad
regression passed. It was not enough to close performance. The failed comparator now writes a
closed, schema-validated failure receipt before returning non-zero. No tolerance was raised and no
freshness/audit-head/rebuild cache was introduced.

## Qualification infrastructure limits

The existing scale runners are not accepted as Core evidence because some fixtures directly write
private SQL/Ledger projection state. Pass 4 records `qualification_fixture_blocked` instead of
manufacturing a pass. No evaluator-only mutation contract was added in this pass.

Still `not_executed`:

- current-candidate Query v6 10k/100k tail lanes;
- Relation/Graph 500/5000/10k/100k lanes;
- Wiki wrong-merge/alias-collision/cycle Core lanes;
- 10,000-request RSS, 8-reader concurrency, cache invalidation, and current-candidate rebuild
  equivalence as independent raw Core reports;
- nine-row Platform Core evidence and Windows-native results;
- exact signed/equivalently verified 28-source Legal Pack;
- repository-external Human Gold and fresh unseen final blind holdout;
- isolated real Codex 0.145.0 / `gpt-5.6-luna` three-run evidence;
- trusted public-redownload evidence.

## External Owner prerequisites

Owner must provide outside the repository:

1. an independently authored and frozen Human Gold plus fresh unseen holdout;
2. an exact signed or equivalently verified Legal Pack;
3. an owner-only isolated Codex evaluation identity/credential;
4. real cross-platform runner evidence and a public-redownload environment.

No repository `.env`, prior DeepSeek key, local Codex Desktop authentication, or
`~/.codex/auth.json` was read, copied or used. OpenCode/DeepSeek remains
`blocked_not_executed` until the old key is revoked and an owner-only evaluation key exists.

## Worker review and integration

Three Luna/Max candidate work packages had exclusive file boundaries: multilingual Context,
Windows/closed environment, and Windows qualification diagnostics. Sol read the complete candidate
diffs, rejected a persistent read-runtime design after broad tests changed integrity/locking
semantics, rejected a large runtime-stability v1 schema expansion, retained only the bounded
alternatives described above, and independently reran focused and full verification. Worker output
was not treated as acceptance evidence.

## Known limitations and not claimed

- Living Wiki performance remains outside the frozen comparator.
- Windows root cause for the earlier `statement_count=0` is not guessed; the next raw report now has
  a bounded diagnostic category.
- Scale/Wiki fixture construction requires a future explicit evaluator decision.
- Twelve provenance-bound Core validators and selective-forget raw evidence are still incomplete;
  assembly remains disabled.
- No Human Gold, final blind, Legal Pack, real Host, complete scale, nine-row Platform Core, or
  public-redownload Core result exists for this candidate.

Not claimed: RC, GA, released, commercial-ready, complete qualification, superiority, SOTA, or
competitive eligibility.
