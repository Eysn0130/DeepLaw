# DeepLaw PRD 1.2 continuity P0 and release disposition

Status: **source candidate, not released** (2026-08-09).

Final disposition:

```text
source_candidate_remains_not_released
release_gate_passed=false
claim_eligible=false
competitive_claim_eligible=false
package_version=0.12.0
```

This report records the local PRD 1.2 continuity P0 remediation, development-protocol rotation,
and v0.13 release-gate correction. It is not a qualification report and does not close the Human
Gold, real-Host, Legal Pack, full current-candidate scale, Timeline, semantic restore,
cross-platform, supply-chain, or publication gates. No version, tag, signature, registry upload,
RC, or GA was created.

Continuity Pass 2 is a narrow correction after the retained **Pass 1** boundary. Pass 1
implementation, historical Gold/protocol inputs, and local evidence remain preserved and
explicitly historical. The continuity correction is commit
`2f31bff4069e6cf01edf017134e5a760becb5360`; the semantic release-evidence correction is commit
`d7da1869287fd590d820f7dd60506abdcb826ad4`. This tracked disposition cannot bind its own final
tree, and no qualification wheel or external report hash exists. The disposition remains not
released.

## 1. Reviewed implementation boundary

| Item | Value |
| --- | --- |
| Branch | `codex/semantic-evidence-package-fix` |
| Task baseline | `72440618cce1a93be8a767d08104e4260c3ee868` |
| Reviewed implementation commit | `ceaaa8e417098e92efcca064604e63945833726e` |
| Reviewed implementation tree | `e2ca644e23fc01a3026350982b4a781711f29c0d` |
| Package version | `0.12.0` |
| PRD 1.2 SHA-256 | `daa524d62471801ca79699948ebca52ab194e14adcdf0bc1d332850fd7a12fb8` |
| Upstream research SHA-256 | `00dfab0dfed139f5d81982061a75896f29552f56a125aa83bec57f0c6a860967` |
| Historical evaluation protocol v1 | `470242a11c4f58a5975c1b576298fcf311bda95af1ebf8f0bfcd4529a4262c8c` |
| Historical repository Gold v1 | `ffce55aabd36738589abc979c903f830baaf18fb6943e218c430079d33de9e97` |
| Frozen sink input v2 | `b3c5c100471cec3a8ecdce115255ae3e4d0d7053800936e5a611fe103527019a` |

The tracked report cannot bind the hash of its own final commit. The final handoff records the
post-report commit/tree and fresh wheel hash. The implementation boundary above is the last runtime,
contract, test, and CI commit reviewed before documentation rotation.

## 2. Core-scope and surface disposition

| Surface | Decision |
| --- | --- |
| `deeplaw knowledge context`, Python `context.compile`, MCP `context` | **KEEP** as the one recommended Agent seam |
| `knowledge query` | **KEEP** as the operator diagnostic seam |
| `knowledge autonomy context` | **SIMPLIFY** as a compatibility/operator alias |
| `deeplaw recall`, `knowledge recall` | **DEFER** removal until consumer inventory and compatibility review |
| `knowledge autonomy recall` | **SIMPLIFY** as operator discovery |
| Query Plan v5 / Capsule v2 | **DEPRECATE** for new consumers; retain explicit compatibility |
| Run Timeline, semantic restore, typed Relation Path | **DEFER** pending external user-task Gold |
| Guides, Codemap, new page families, connectors, Host runtime | **DEFER** outside v0.13 core freeze |

No Knowledge kind, Relation predicate, Authority dimension, database, Host adapter family, Graph
engine, connector, or UI was added. The only new persistent structure is a derived, rebuildable,
bounded route projection inside the existing Knowledge store.

## 3. P0 reproductions, root causes, risks, and repairs

### P0-A — task binding filtered after global Top-20

The public Python/CLI/MCP v6 path reproduced a missing exact working checkpoint when 25 or more
same-project, similar-task lines competed in ordinary Revision discovery. The requested route could
fall outside the ordinary `recall(limit=20)` set before binding admission ran.

- Root cause: task-line routing was treated as a late content filter instead of a separate
  eligibility route.
- Impact: first correct action and decision preservation could depend on row/import/rank order.
- Authority/privacy risk: raising the global limit would still be position-dependent and could
  expose more unrelated candidates.
- Minimum repair: `knowledge_checkpoint_routes_v1` is a derived indexed projection queried before
  ordinary discovery. It has explicit route/task/snapshot indexes, bounded lookups, bounded rebuild,
  canonical Run/Revision/Event revalidation, and integrity checks. Exact checkpoint admission does
  not widen the ordinary public selected-Revision count or admit other task-line content.

Development diagnostics cover 25 exact task lines and indexed 10k/100k route-table shapes with
`EXPLAIN QUERY PLAN` plus bounded `LIMIT 20`. These are index/bounded-query diagnostics, not full
100k product or commercial qualification.

### P0-B — workspace evolution silently became `no_answer`

A checkpoint bound to route R and snapshot S1 was reproduced as silently absent after the same
route advanced to S2.

- Root cause: one exact binding digest conflated stable routing identity with mutable workspace
  snapshot.
- Impact: normal commit/dirty-state evolution looked like another task line and erased continuity
  without a stale-state signal.
- Privacy risk: naive mismatch diagnostics could reveal branch, path, diff, or another task line.
- Minimum repair: route identity now binds Vault plus opaque project, repository,
  stable-worktree, and task-line identifiers; snapshot separately binds base revision and
  dirty-state digest. Same route/same snapshot may admit current working state. Same route/different
  snapshot withholds it and emits bounded `workspace_diverged`. Different route fails closed
  without an existence oracle. Parent/fork identity is not derived from exact binding-JSON equality.

Absolute paths, branch names, current commits, remote URLs, Host session IDs, Secrets, and binding
objects are recursively removed from Provider output. A binding is neither identity authority nor
a capability.

### P0-C — ordinary cold-thread entry required manual canonical JSON

The audit confirmed that the prior adapters did not derive/inject task binding, the CLI required
manual opaque values, and task text alone could not restore a checkpoint through the recommended
entry.

- Root cause: the kernel had a closed binding contract but no shared cold-request resolver.
- Minimum repair: the Host-neutral task-context module owns normalization and route/snapshot
  derivation. Thin adapters do not duplicate identity business logic. Without a binding, an exact
  task-text digest can recover only one uniquely admitted route in the selected Vault; multiple
  routes return `task_line_ambiguous`, and newest-wins is prohibited.
- Remaining boundary: this is an exact-match development resolver, not semantic Timeline search or
  real-Host stable-identity enrollment. Therefore the complete cold-thread workflow remains
  `Target`, not `Implemented` or `Qualified`.

### Continuity Pass 2 — three reproduced defects and minimum repairs

Pass 2 keeps the Pass 1 scope and compatibility boundary and records only kernel-level
development evidence:

| Root cause | Minimum repair | Boundary |
| --- | --- | --- |
| Exact route candidate could be displaced by ordinary selection | Reserve one exact route candidate as an independent bounded admission partition. The no-route ceiling remains `512`; one reservation leaves at most `511` ordinary candidates, and the combined/global and final Capsule budgets do not increase. | Kernel `Implemented`; E2E `Target`; external qualification `not_executed` |
| Retrieval `goal` changed route identity | Retrieval query is `task + goal`; the route digest is generated only from canonical task text inside the domain, never by an adapter or caller. | Kernel `Implemented`; E2E `Target`; external qualification `not_executed` |
| One route could expose multiple current heads | First route write creates one Knowledge Object. Later writes create a new revision with `expected_revision` CAS; stale/concurrent writes fail as `checkpoint_head_conflict`. A pre-fix multi-head read returns only a sanitized Gap. Owner reconciliation uses existing `forget`/withdraw plus derived projection rebuild; LWW is forbidden. | Kernel `Implemented`; E2E `Target`; external qualification `not_executed` |

The route projection remains derived/rebuildable. The continuity correction adds no new canonical
Knowledge table, migration, or sink schema, and `knowledge-sink.input/v2` bytes remain unchanged.
This is a semantic compatibility
boundary only: legacy bytes/history remain immutable and verifiable while new checkpoint writes
enforce one route/one current head.

Core gates are not lowered. Capability gates may remain `not_claimed` when not declared (Run
Timeline, semantic restore, and Claude/OpenCode remain deferred unless support is explicitly
declared). The Competitive Claim gate is independent of this kernel evidence and remains false.

## 4. Identity and admission invariant

```text
task-line routing identity
  != workspace/checkpoint snapshot
  != Run identity
  != Host session/thread/memory reference
  != capability/authorization
```

Project/repository/worktree/task-line values must be owner-registered portable opaque identities.
Absolute path hashes, branch names, current commits, remote URLs, task text alone, and embedding
similarity are not sufficient identities. Host references are untrusted hints and must be rebound
through Vault/project/worktree admission. A branch rename, commit, or dirty-state change does not
create a new task line, but snapshot divergence prevents stale state from being asserted as current.

## 5. Sink compatibility and reconciliation

The released `knowledge-sink.input/v2` bytes and SHA remain unchanged. A legacy v2 client may still
record an unbound Run. The additive `knowledge-sink.input/v5` contract is the current bound-write
seam; v3 and v4 remain frozen.

An unbound legacy Run/checkpoint stays immutable and verifiable but is withheld from default v6
current working context. Owner-controlled reconciliation records a new bound Run and an
attributable successor Revision using the existing coordinator. Tests prove old Run, Revision,
event, and content bytes are not rewritten, and an old snapshot produces a divergence Gap. This is
a semantic migration boundary even though it needs no new canonical table.

## 6. Repository Gold rotation

Repository Gold v1 and evaluation protocol v1 remain historical and immutable. v1 is expected to
reject current changed source bytes; that rejection is tested as a historical boundary.

The default local runner now uses repository-visible development Gold v3 and evaluation protocol
v2. Their labels, answers, hashes, and scorers are visible in the repository. Every v2 report is
fail-closed with `quality_protocol_eligible=false`; a clean tree or candidate wheel cannot turn it
into external Human Gold, a qualification holdout, or a release artifact. The rotation did not
skip, xfail, weaken, or overwrite a v1 expected answer.

## 7. v0.13 commercial release gate

Release policy is version-conditional and fail closed:

- versions below 0.13 retain historical manifest-v5 compatibility;
- every `0.13.x` release requires commercial manifest v6;
- an unknown later version has no implicit fallback.

Pass 2 replaces the old hash-only v6 decision shape with three layers:

1. `commercial-evidence-report/v1` contains closed observations, exact candidate/protocol/
   threshold/Gold bindings, exact command/environment/run count, metrics, frozen hard-zero
   counters, failure inventory, redaction counters, and independently hashed observation content.
   It contains no caller-supplied `passed`, release, or claim decision.
2. `v013-release-gate-classification/v1` freezes Core, Capability, and Competitive Claim gate IDs,
   minimum run counts, model requirements, metric bounds, and exact hard-zero inventories. The
   deterministic semantic validator rejects weakened thresholds, omitted counters, stale wheel or
   protocol bindings, development-as-blind claims, canaries, and private absolute paths.
3. `v013_commercial_release` derives manifest v6 only after semantic validation. `release_policy`
   then checks the closed envelope, exact asset bindings, candidate invariants, and derived gate
   statuses; it does not reinterpret caller booleans as evidence. Publish and public-redownload
   paths rerun semantic validation before envelope admission.

Core gates cover canonical integrity, migration/recovery, host/secret isolation, bounded Context,
legal Evidence/Authority and exact source/citation/locator, required scale/performance, supported
platforms, reproducible supply chain, external Human Gold isolation, real Codex acceptance, and
selective forget. Timeline, semantic restore, Claude, and OpenCode are Capability gates and may be
`not_claimed` only when support is not declared. Competitive comparisons remain separately
`not_claimed`. No v6 evidence manifest was generated for this source candidate.

## 8. Pass 1 local verification (historical boundary)

The reviewed implementation commit produced:

```text
uv lock --check
  PASS

uv run --frozen pytest --strict-markers
  PASS — 1253 passed, 9 skipped in 328.52s

uv run --frozen ruff check .
  PASS

git diff --check
  PASS
```

Focused development evidence includes route-first public-seam parity, 25-line exact retrieval,
snapshot divergence, recursive Provider redaction, v2 legacy reconciliation, thin-adapter parity,
repository-development protocol rotation, and version-conditional release negative tests. The nine
skips remain visible and cannot satisfy the v0.13 zero-mandatory-skip gate.

The documentation/development-freeze commit
`cf88b55d35a93280475692fbfc3bc8c0201b7f9f` (tree
`d555c13f44656f4ef5765e5629169326fae22ebf`) independently produced:

```text
uv lock --check
  PASS — Resolved 140 packages
uv run --frozen pytest --strict-markers
  PASS — 1253 passed, 9 skipped in 334.18s
uv run --frozen ruff check .
  PASS
git diff --check
  PASS
```

Two builds with `SOURCE_DATE_EPOCH=1786219200` were byte-identical. The local reproducibility
record SHA-256 was `af9969d4bc89896c36a9ce665cba44d30452b641a745aeaf8a73efaaa282e5ce`;
the wheel SHA-256 was `959cfebadeebba3599083de20cf4ff6c02ecb40f731f8a946239dbf3aee534f0`
and the sdist SHA-256 was
`610181e3eb8d3258eb7dcecd3e6ddd8e403e4f03d902f94892ce09b5ac7f6515`.
An isolated Python 3.13 environment installed that wheel, imported DeepLaw, found packaged sink
input v5, and passed the wheel filename/private-path-marker scan. This is same-machine local build
evidence, not SBOM/provenance/public-redownload or cross-platform release qualification. Final
post-report commit/tree and fresh artifact hashes remain part of the external handoff because a
tracked report cannot bind its own bytes. These are Pass 1 historical handoff references.

### Pass 2 local verification

The pre-documentation Pass 2 working tree, containing the two commits above plus the explicit
repository-visible development-Gold hash rotation, produced:

```text
uv lock --check
  PASS

uv run --frozen pytest --strict-markers -rs
  PASS — 1270 passed, 9 skipped in 344.31s

uv run --frozen ruff check .
  PASS

git diff --check
  PASS
```

Focused Continuity tests covered the 520-Statement route reservation, task with/without goal at
Python/CLI/MCP seams, S1→S2 progression, same-snapshot successor revision, stale CAS conflict,
projection rebuild, withdrawal, multi-head fail-closed Gap, and Provider redaction. Focused release
tests covered actual report-byte validation, exact candidate/protocol/threshold/Gold bindings,
frozen metric/hard-zero inventories, development-as-blind rejection, canary/path rejection,
decision-free assembly, and publish/public-redownload semantic revalidation. These are local
development results, not external qualification.

## 9. Capability status

| Capability | Current | Qualification boundary |
| --- | --- | --- |
| Continuity/Context | `Target` workflow; route/snapshot/admission kernel implemented in development | Semantic cold-start, Human Gold, First Correct Action, native-memory comparison, fork lifecycle, real Hosts absent |
| Living Wiki | Implemented development chain | Independent human usability, current-candidate scale, typed path task absent |
| Protected/Legal Evidence | Implemented local read boundary | Exact signed/verified Pack, independent legal Gold, temporal/exception primary-evidence run absent |
| Host Integration | Target with static/thin-adapter tests | Real isolated Codex/Claude/OpenCode and canary/model acceptance absent |
| Portability/Operations | Target with local primitives | Timeline, semantic restore, full migration/recovery, 3 OS, supply chain, public redownload absent |

### Gate classification

| Gate class | Rule | Pass 2 disposition |
| --- | --- | --- |
| **Core** | Required; no safety, integrity, legal, boundary, scale, platform, or supply-chain gate may be lowered | Unchanged and not lowered; required external evidence remains `not_executed` |
| **Capability** | May remain `not_claimed` when not declared | Run Timeline, semantic restore, and Claude/OpenCode remain deferred/not_claimed unless explicitly supported; continuity E2E remains `Target` |
| **Competitive Claim** | Independent named-comparator/host evidence; separate from Core and Capability | `competitive_claim_eligible=false`; no local kernel evidence satisfies this gate |

No capability is `Qualified` or `Released`.

## 10. Delivery inventory

| Requested delivery | Disposition |
| --- | --- |
| PRD traceability and Current/Target/Qualified/Released matrix | Updated in `docs/PRD_TRACEABILITY_MATRIX.md` |
| deletion/addition and duplicate-surface audit | Delivered in section 2 |
| task-lineage/concurrency and Context stale/disambiguation | Pass 1 plus Pass 2 bounded kernel repairs delivered (route reservation, task/goal identity separation, one route/one current head CAS/recovery); external qualification absent |
| Run Timeline | Missing owner public seam already reproduced; deferred pending external Gold |
| Vault isolation | Existing default physical isolation development evidence retained; explicit cross-Vault lifecycle not executed |
| Wiki ownership/reconciliation/typed relation | Existing local chain retained; independent human task not executed |
| semantic restore/recovery | Not implemented; snapshot rollback is not semantic Revision restore |
| source acquisition | Existing allowlist/snapshot primitives only; external acquisition task not executed |
| credential/Host isolation | Deterministic canaries retained; real Hosts blocked/not executed |
| poisoning/selective forgetting | Local regressions retained; Human Gold and release artifact lane not executed |
| legal retrieval | Local boundary retained; exact Pack/Human Gold not executed |
| Human Gold, real Hosts, full scale/RSS/concurrency, 3 OS | `not_executed` |
| migration/backup/restore | Local legacy reconciliation passed; full release-artifact matrix not executed |
| wheel/SBOM/provenance/public hashes | Fresh local wheel deferred to final handoff; release SBOM/provenance/public redownload not executed |

## 11. Not executed and known limitations

### Pass 2 skip disposition

These nine lanes are explicit non-results; a skip is never treated as a pass:

| Required lane | Disposition |
| --- | --- |
| Statement scale 10k | `required not_executed` |
| Statement scale 100k | `required not_executed` |
| Relation truncation 500/5000 | `required not_executed` |
| Wiki wrong merge | `required not_executed` |
| Wiki alias collision | `required not_executed` |
| Wiki cycle | `required not_executed` |
| Historical v0.6 wheel | `separate compatibility not_executed` |
| Windows native ACL | `macOS not_applicable`; Windows evidence remains required |
| Windows native junction | `macOS not_applicable`; Windows evidence remains required |

- repository-external independent continuity/Wiki/legal Human Gold;
- qualification and fresh final-blind holdouts;
- Host-only / Host-native Memory / Host-native Memory + DeepLaw equal-budget comparison;
- real Codex, Claude Code, and OpenCode/DeepSeek, three runs each;
- exact signed and verified 28-source Legal Pack;
- full current-candidate Statement/Relation/Wiki scale, 10,000-request RSS, eight-reader, and cache
  lanes (only bounded route-index diagnostics ran);
- Run Timeline, semantic restore, fork merge/conflict reconciliation, and authorized cross-Vault
  lifecycle;
- clean install/uninstall and full existing/failed migration recovery from a release artifact;
- Linux and Windows and the full 3 OS × Python 3.11/3.12/3.13 matrix;
- release SBOM, license/OpenVEX review, provenance, public upload/redownload, tag, signature,
  registry publish, RC, and GA.

Accordingly, repository-external Human Gold, the exact signed/verified Legal Pack, real Host
evidence, current-candidate scale, the full three-OS matrix, and supply-chain qualification are all
`not_executed`; none is inferred from the Pass 1 or Pass 2 development regressions.

The exact task-text resolver does not perform semantic task lookup. Stable portable route-ID
enrollment is not real-Host qualified. The derived route table has bounded synthetic index evidence,
not full product-scale evidence. Legacy unbound state is historical only until the Owner creates a
new bound Run and successor Revision. Timeline and semantic single-Revision restore remain absent.

No project `.env`, current `~/.codex/auth.json`, Codex Desktop login state, or old DeepSeek key was
read, copied, printed, mounted, or used. No real Provider call occurred. OpenCode/DeepSeek remains
`blocked_not_executed` until the Owner revokes the old key and supplies repository-external,
owner-only, evaluation-only credentials. Internal correctness, Gold, workflow, Timeline, restore,
and qualification work also remains, so the overall disposition is not
`blocked_by_external_credential_or_infrastructure`.

## 12. Not claimed and next sequence

Not claimed: complete PRD implementation, complete cross-thread continuity, complete Wiki
usability, legal correctness, exact-Pack success, real-Host success, full scale, cross-platform
support, commercial readiness, competitive advantage, SOTA, RC, GA, or release.

The next admissible sequence is: independent authors freeze external Human Gold and isolation
boundaries; run the three equal-budget Host lanes only after Owner credentials and canary preflight;
qualify human Wiki and exact legal evidence; then, and only if core tasks pass, execute current
candidate scale, migration/recovery, cross-platform, supply-chain, and reproducible-public-artifact
gates. Formal publication still requires a separate final Owner approval.
