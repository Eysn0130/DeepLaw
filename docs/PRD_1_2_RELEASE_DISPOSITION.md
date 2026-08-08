# DeepLaw PRD 1.2 implementation and release disposition

Status: **source candidate, not released** (2026-08-08).

Final disposition:

```text
blocked_by_external_credential_or_infrastructure
release_gate_passed=false
claim_eligible=false
competitive_claim_eligible=false
package_version=0.12.0
```

This report closes the authorized local PRD 1.2 audit and one reproduced minimum remediation. It
does not close the repository-external Human Gold, real-Host, exact Legal Pack, current-candidate
scale, 3-OS, SBOM/provenance, or publication gates. No version, tag, signature, registry upload,
RC, or GA was created.

## 1. Exact candidate boundary

| Item | Value |
| --- | --- |
| Branch | `codex/semantic-evidence-package-fix` |
| PRD adoption commit | `a0a2a26c456fb553490cbe517d8d7d799ae137ea` |
| Reviewed implementation commit | `eb36b948eb8d43e3bc556a78220375774a95d95d` |
| Implementation tree | `f9ce4e793873d45680c44df1ed71e7145699be72` |
| Package version | `0.12.0` |
| Development wheel SHA-256 | `45b6ba427f1c73eebaeb2f93da232def8b2992ec68f251980128791ef616ddc4` |
| PRD 1.2 SHA-256 | `daa524d62471801ca79699948ebca52ab194e14adcdf0bc1d332850fd7a12fb8` |
| Upstream research SHA-256 | `00dfab0dfed139f5d81982061a75896f29552f56a125aa83bec57f0c6a860967` |
| Traceability matrix SHA-256 | `4f790eaa8f1dcfbf10798d34039426aff560f6d892ae110ae9ee866ee33e25c1` |
| Development reproduction report SHA-256 | `3d37d4c51fa096da40f6fdbee0dff1097ef53ee4a15663cc3efd5efbd19e5a0d` |
| Task-binding contract report SHA-256 | `16c285fd993026814f00a7951b44ada111a238bac0be16d91229ce77bf5d08b2` |

The wheel was built twice from the clean implementation commit on the same machine; both builds
had the recorded hash. An isolated Python 3.13 environment installed the exact wheel, found the
packaged task-binding Schema, wrote a task-bound checkpoint, and passed store integrity
verification. The wheel scan found no owner-local path marker, `.env`, or `auth.json` filename.
This is a local reproducibility smoke test, not multi-platform release provenance.

Environment for the final local checks:

```text
OS=Darwin 26.5.2
kernel=25.5.0
architecture=arm64
Python=3.12.13
SQLite=3.50.4
```

## 2. PRD adoption and traceability

`docs/PRD_TRACEABILITY_MATRIX.md` maps all 107 unique PRD IDs. Its status vocabulary is limited to
`Target`, `Implemented`, `Qualified`, `Released`, `Deferred`, and `Not Implemented`; it contains no
`Qualified` or `Released` row.

The PRD and upstream research bytes match the Owner-provided reference hashes. Frozen repository
Gold files and hashes were not rewritten to make the modified runtime appear unchanged. The
pre-remediation `repository-gold-v1` therefore correctly rejects the new
`src/deeplaw/knowledge_autonomy.py` bytes. Starting a new Gold freeze requires an explicit new
protocol version and new fixture; it is not performed or implied here.

## 3. Deletion/addition and duplicated-surface disposition

No command, compatibility contract, Knowledge kind, Relation predicate, Authority dimension,
database, page family, Host adapter, or connector was deleted or added beyond the reproduced
task-binding seam.

The existing scope audit remains the product disposition:

| Surface | Disposition |
| --- | --- |
| `knowledge context` across Python/CLI/MCP | **KEEP** as the single recommended Agent entry |
| `knowledge query` | **KEEP** as the operator diagnostic surface |
| `knowledge autonomy context` | **SIMPLIFY** as a compatibility/operator alias |
| `deeplaw recall` and `deeplaw knowledge recall` | **DEFER** removal pending consumer inventory |
| `knowledge autonomy recall` | **SIMPLIFY** as internal/operator discovery |
| explicit v5 / Capsule v2 and MCP recall | **DEPRECATE** for new consumers, retain compatibility |
| Run Timeline, semantic restore, typed Relation Path | **DEFER** until their external task/Gold gates |
| Guides, Codemap, new UI/page families/connectors | **DEFER** outside the v0.13 core freeze |

No compatibility surface was physically removed in this candidate.

## 4. Reproductions and minimum remediation

### Task-line/worktree contamination

`PRD12-REPRO-CONT-010-001` reproduced wrong current-state admission through the public Python
Context seam with two same-task current checkpoints. A second fixture used real linked Git
worktrees with different base revisions and dirty-state digests. An isolated installation of the
exact pre-remediation wheel reproduced the same defect, closing source-tree import ambiguity.

Root cause:

```text
Run identity != task-line identity != repository/worktree state
```

The existing Run Record and Query v6 admission could prove writer/scope/sensitivity/lifecycle but
had no exact current project/task/worktree selector. The minimum correction adds one closed,
opaque `deeplaw.task-context-binding/v1` object and reuses the existing Run metadata, receipt,
event, Query Plan, and Capsule primitives. It adds no table or mutation coordinator.

Post-fix behavior:

- new working memory requires an immutable successful, task-bound Run Record;
- `failed`, `partial`, and `aborted` Run statuses were separately reproduced as incorrectly
  accepted and are now rejected for current working state;
- Query Plan v6 and local Capsule v3 bind the exact selector or explicit absence;
- no selector withholds working checkpoints and returns `task_binding_required`;
- legacy-unbound state is withheld; an exact selector admits only the matching line;
- a mismatched line remains a local rejection and does not create a Provider-visible existence
  oracle;
- Provider v2 is unchanged, excludes all binding fields, and retains the 65,536-byte hard limit;
- Python, CLI, and MCP v6 query/context use the same domain path; v4/v5 reject rather than discard
  a binding;
- ordinary reads remain Ledger-write-free.

Luna workers changed only Task Card-owned files. The Sol integrator read every candidate diff,
corrected integration and privacy issues, and independently reran the public-seam, contract,
continuity, CLI/MCP/Python parity, integrity, tamper, and full repository tests. No credential,
external Host, Git decision, migration disposition, or release decision was delegated.

### Run Timeline

`PRD12-REPRO-CONT-012-001` reproduced a missing owner public seam for finding an older Run by task
meaning, time, status, and Artifact without knowing its Run ID. Existing Run Records, events,
Checkpoints, Artifacts, and Query Trace are useful primitives, but there is no unified owner-only
Timeline with semantic/status/time/Artifact filters, cursor pagination, and forget linkage.

No Timeline was implemented because repository-external time-to-locate Gold and disclosure/
deletion expectations are absent. Full transcript storage remains forbidden.

### Vault isolation

`PRD12-REPRO-KNOW-010-001` did not reproduce default physical cross-Vault query, identity merge,
or CWD-based disclosure. Two Vaults with the same semantic key and task-line digest remained
isolated when each request used its explicit Vault root and project binding. No cross-Vault feature
or fix was manufactured. Explicit cross-Vault imports/references and independent backup/forget
operations remain unqualified.

## 5. Capability status

| Capability | Current status | Local evidence | Qualification blocker |
| --- | --- | --- | --- |
| Continuity/Context | `Implemented` in part | exact-line admission, successful Run gate, Python/CLI/MCP parity, Provider redaction | prior context-density gate failed; no independent Gold, native-memory comparison, fork reconciliation, or real Host |
| Living Wiki | `Implemented` development chain | Source→Revision/Fragment/Locator→Knowledge/Relation→Ledger→Registry/Link/Resolver→Wiki→Context focused tests exit 0 | three explicit Wiki skips, no independent human task, typed Relation Path and current-candidate scale absent |
| Protected/Legal Evidence | `Implemented` local read boundary | focused legal/evidence tests exit 0; hard Authority/version/quote rules remain contracts | exact signed 28-source Pack and independent legal Gold absent; prior development qualification admitted no current/exception primary evidence |
| Host integration | local deterministic only | closed-environment canary suite exit 0 | evaluation-only identities/keys, blind source and three runs per Host absent |
| Portability/operations | partial local implementation | clean wheel smoke, same-machine repeat hash, existing snapshot/recovery tests | semantic restore absent; current SBOM/provenance/redownload, migration install, 3 OS/Python matrix absent |

One capability being implemented does not qualify the product or another capability.

## 6. Wiki, Relation, Context, and legal boundary

The focused Wiki/legal command completed with exit 0 and three explicit Wiki skips. It covers the
existing human/Agent evidence chain, page identity, links, resolver, rebuild/ownership and local
legal exact-evidence contracts. The result remains development evidence:

- Wikilinks/backlinks are navigation and do not create typed Relations or Authority;
- Source bytes remain immutable; governed edits create revisions;
- Agent legal interpretation remains `origin=agent_derived` and `legal_authority=false`;
- local regressions retain zero-tolerance rules for false Authority, wrong-version primary
  evidence, invalid Quote/Locator primary evidence, Source mutation, and private-path disclosure;
- the exact signed Pack did not run, so those exact-Pack observed counts are `not_executed`, not
  reported as zero;
- Guides, Codemap, materialized path pages, complete `as_of` Wiki, and single-Revision pointer
  rewind were not implemented;
- semantic restore by a new attributable revision remains `Not Implemented` pending an external
  rollback task and dependency/recovery contract.

The current-candidate 5k/10k/100k Statement, 10k/100k Relation, Wiki scale, 10,000-request RSS, and
8-reader gates were intentionally not substituted for missing Human Gold. Historical reports bind
older commits and are not relabelled as evidence for `eb36b948...`.

## 7. Credential, Host, and repository safety

No project `.env`, DeepSeek key, current Codex Desktop authentication, or `auth.json` was read,
copied, mounted, printed, or used. No real Provider or network model call occurred. The focused
closed-environment Host tests passed 13 local tests and keep ambient/provider canaries out of Host,
MCP, argv, prompt, stdout, stderr, report, and Artifact surfaces.

The repository secret-candidate scan explicitly excluded `.env` and inspected 821 tracked or
candidate files. It classified three existing synthetic test canaries and found zero unclassified
secret candidates. This is a bounded pattern scan, not a substitute for release security review.

OpenCode/DeepSeek remains `blocked_not_executed` until the Owner revokes the previously exposed key
and provides a new repository-external owner-only evaluation secret. Real Codex/Claude/OpenCode
also require isolated evaluation identities, blind source, frozen Human Gold, exact versions, and
resolved-config/tool/egress proof.

## 8. Exact local commands and results

Executed from the repository root on the implementation commit unless stated otherwise:

```text
uv lock --check
  PASS — Resolved 140 packages

uv run --frozen ruff check .
  PASS — All checks passed

git diff --check
  PASS

uv run --frozen pytest --strict-markers
  FAIL — 1185 passed, 9 skipped, 4 failed in 254.10s
```

All four failures are the same intentional frozen-Gold boundary:

```text
repository Gold Set source hash changed: src/deeplaw/knowledge_autonomy.py
```

Affected tests are two evaluation-protocol report-package cases and two repository-Gold quality
cases. The old expected hash was not changed, and the failures were not skipped, weakened, or
relabelled as passes. All other tests in the run passed.

Additional focused results:

```text
task-binding + task-line + real-worktree + v6 parity: PASS
autonomy + sink MCP + read MCP + CLI + v6 contracts: PASS
core continuity + continuity development benchmark: PASS
Wiki + evidence + legal focused suite: PASS with 3 explicit Wiki skips
Host environment/cross-host deterministic suite: 13 passed
installed Python 3.13 wheel smoke: PASS
same-machine two-wheel hash equality: PASS
wheel private-path/auth-filename scan: PASS
```

Protocol/scorer bindings:

| Artifact | SHA-256 |
| --- | --- |
| evaluation protocol v1 | `470242a11c4f58a5975c1b576298fcf311bda95af1ebf8f0bfcd4529a4262c8c` |
| v0.13 qualification protocol v1 | `95283e2d1fdd60a429941c6ab718cebd739ad414ddc38d58b3f2fcc14f4cffb5` |
| continuity scorer | `96e1520f80e2115cf36a3ec951c1ab1103549236a4b10896944d1e64dee95941` |
| Evidence Wiki scorer | `4f70cc2558280b29567bcdef948c188fd52de3a48bb9cd45ea723821936e1849` |
| legal exact-evidence scorer | `e4ec93d11a638faafc6137724459d6695c5702649cda6f1284aa2943d6ba4a2d` |

No qualification/final-blind corpus, Human Gold, Host/model result, peak RSS, current SBOM, current
provenance statement, or public-redownload hash exists for this candidate.

## 9. Required delivery inventory

| Requested delivery | Disposition |
| --- | --- |
| 1. PRD traceability matrix | Delivered: `docs/PRD_TRACEABILITY_MATRIX.md` |
| 2. deletion/addition audit | Delivered by PRD research plus this report; no extra runtime surface admitted |
| 3. capability matrix | Delivered above; no `Qualified`/`Released` capability |
| 4. task-lineage/concurrency report | Development reproduction and minimum exact-line remediation delivered; fork merge not executed |
| 5. Run Timeline report | Missing seam reproduced; implementation deferred |
| 6. Vault isolation report | Default physical leak not reproduced; explicit cross-Vault lifecycle not executed |
| 7. Wiki ownership/reconciliation report | Existing local reports and focused regression pass; independent human task absent |
| 8. graph/path/relation report | Existing smoke evidence only; true path API and current 10k/100k Relation not executed |
| 9. Context tail/stale/disambiguation report | Historical old-commit reports retained; exact task-line disambiguation added; current scale not executed |
| 10. semantic restore/recovery report | Semantic single-Revision restore not implemented; snapshot recovery remains distinct |
| 11. source acquisition report | Existing explicit allowlist/snapshot primitives pass local tests; external acquisition task absent |
| 12. credential/Host isolation report | Deterministic canary pass; real Hosts blocked/not executed |
| 13. poisoning/selective-forget report | Existing local regressions pass; lifecycle Human Gold absent |
| 14. legal retrieval report | Local regression report retained; exact Pack/Human Gold not executed |
| 15. real Codex/Claude/OpenCode reports | `not_executed` |
| 16. Human Gold manifest | `not_executed`; no repository-external approved Gold provided |
| 17. scale/RSS/concurrency report | Historical older-commit reports retained; current candidate not executed |
| 18. migration/backup/restore report | No physical task-binding migration; local existing tests pass; release-wheel migration matrix not executed |
| 19. exact commands/environment | Delivered above |
| 20. commit/wheel/SBOM/provenance hashes | Commit/tree/wheel delivered; current SBOM/provenance/public-redownload `not_executed` |
| 21. not_executed | Listed below |
| 22. known limitations | Listed below |
| 23. final release disposition | `blocked_by_external_credential_or_infrastructure` |

## 10. Not executed

- repository-external independent continuity/Wiki/legal Human Gold;
- qualification and fresh final-blind holdouts;
- Host-native Memory three-lane comparison;
- real Codex, Claude Code, and OpenCode/DeepSeek, three runs each;
- DeepSeek model/config/tool/JSON/timeout/rate/receipt/secret preflight;
- exact signed and verified 28-source Legal Pack;
- current-candidate 5k/10k/100k Statement, 10k/100k Relation, Wiki scale, RSS, 8-reader,
  current cache-invalidation and Timeline rotation lanes;
- fork merge/conflict reconciliation, Run Timeline, semantic restore, and authorized cross-Vault
  import/reference/export lifecycle;
- clean first install/uninstall, existing-Vault migration matrix, and failed-migration recovery
  from the release artifact;
- Linux and Windows runners; complete Python 3.11/3.12/3.13 matrix;
- release SBOM, provenance, signed artifact, public upload/redownload, and external hash verification;
- tag, signing, registry publication, RC, and GA.

## 11. Known limitations and not claimed

- The task binding is an admission selector, not a capability or authorization token. Stable
  project/task/worktree digest derivation is a Host/owner integration responsibility and is not
  real-Host qualified.
- `parent_task_lineage_sha256` preserves an opaque parent reference but does not implement fork
  merge, conflict reconciliation, or scheduling.
- Legacy unbound Run Records remain verifiable history but cannot ground current working context.
- The content-minimized Run Timeline and semantic single-Revision restore remain absent.
- The old repository Gold correctly fails against changed runtime bytes; no new protocol freeze
  was created after observing current results.
- Prior continuity density, legal primary-evidence, Wiki skip, scale, and portability limitations
  remain in force.

Not claimed: complete PRD implementation, complete cross-thread continuity, complete Wiki
usability, legal correctness, exact-Pack success, real-Host success, cross-platform support,
commercial readiness, competitive advantage, SOTA, RC, GA, or release.

## 12. Next authorized sequence

1. Owner revokes the exposed DeepSeek key and supplies repository-external evaluation-only
   credentials/identities through owner-only files; no repository or Desktop auth reuse.
2. Independent authors freeze continuity, Wiki and legal Human Gold plus qualification/final-blind
   corpora before candidate output is read.
3. Run equal-budget Host-only, Host-native Memory, and Host-native Memory + DeepLaw on isolated
   Codex/Claude/OpenCode, three times each, with deterministic scoring.
4. Only if product tasks pass, run the exact current-candidate scale, migration, recovery,
   cross-platform, SBOM/provenance and public-redownload gates.
5. Rotate frozen repository Gold only through an explicit new protocol version and fixture; never
   edit v1 hashes to conceal changed inputs.
6. Submit the complete evidence package and wait for the Owner's separate explicit publish
   approval before any tag, signature, or registry upload.

Until then, the correct decision is to keep the committed implementation as a non-released source
candidate and stop scope expansion.
