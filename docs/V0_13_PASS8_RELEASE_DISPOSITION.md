# DeepLaw v0.13 Pass 8 release disposition

Status: **source candidate only; not released** (2026-08-11).

This report closes the bounded Pass 8 construction work that can be proven locally. It does not
convert Development evidence into qualification evidence. The package remains `0.12.0`; no tag,
RC, GA, signature, publication, or competitive claim is authorized.

## Exact source coordinates

| Coordinate | Value |
|---|---|
| frozen input branch | `codex/v013-evidence-provenance` |
| frozen input commit | `cae4bdf2a91e1a2cf828fa7c6e7b081313632bba` |
| frozen input tree | `fb2e4d104af0917c2aedc4b77f4ac89f4c55b6db` |
| Pass 8 branch | `codex/v013-pass8-lean-qualification` |
| final code candidate commit | `2a635d228e99537304282223ae08ef066a4961e2` |
| final code candidate tree | `566b2f546816264d997f1418c61be6c25cdb2494` |
| exact runtime-evidence commit/tree | `ea0a44c0b76f9ec23bb3482feea1bd621e0b1df7` / `8e09bc6eb648a6ccbb2c1e2dfeb2addf577221c2` |
| package version | `0.12.0` |
| one-time rule-freeze commit/tree | `4befa479a063e2c022814d8d9f15feeeecbee5b9` / `7e8602eb9ecaee6f16044d3786d8623ea3cb50ab` |
| frozen `AGENTS.md` SHA-256 | `e8d14f80295a4e923b72b51a54b6d189d953a640e5771f8d2577d036c5296514` |

The repository-visible Development Gold binding was updated once to that final `AGENTS.md` hash.
`AGENTS.md` was not changed again. The only change after the runtime-evidence commit retired a
stale 5,001-Relation diagnostic reason; it did not change packaged runtime source. The final wheel
was nevertheless rebuilt and clean-installed from the final code candidate.

## Sibling reuse and notices

The complete field-level manifest is
[`UPSTREAM_REUSE.md`](UPSTREAM_REUSE.md); the outcome matrix is
[`V0_13_PASS8_CAPABILITY_GAP_MATRIX.md`](V0_13_PASS8_CAPABILITY_GAP_MATRIX.md), and distribution
notice status is in [`THIRD_PARTY_NOTICES.md`](../THIRD_PARTY_NOTICES.md).

- OpenWiki is frozen at `7531d615216e8cbccf464f66cfbbae3668871c84`, version `0.3.1`, MIT.
- Tolaria is frozen at `ab01faa6773136a58285d04cb81e2587c11bac85`, published
  AGPL-3.0-or-later.
- Verbatim copied upstream source: **none**.
- Adapted upstream implementation: **none**.
- Actual reuse consists of independently re-authored behavioral fixtures, architectural reference,
  and a development-only probe that imports Tolaria from a separate exact checkout.
- No OpenWiki/Tolaria runtime, Node/Tauri/React/Rust dependency, telemetry, connector, Secret model,
  Authority, Ledger, or mixed read/write MCP was added to DeepLaw Core.
- No MIT/AGPL source fragment is incorporated into the wheel or sdist, so there is no new
  incorporated-code notice. The external Tolaria dependency audit has six known high findings;
  none are redistributed by DeepLaw.
- Tolaria rights basis is the Owner-declared same-team authorization. A separate-grant reference or
  irreversible summary and actual file/contributor coverage remain mandatory before any
  Apache-2.0 artifact distributes Tolaria-derived source. No unrecorded oral authorization is
  treated as the sole commercial basis.

## Context retrieval and Provider projection

An isolated Python 3.11 runtime installed the exact candidate wheel and used the first-party CLI,
Python facade, MCP `knowledge_context`, and receipt explain surfaces. The repository-visible Gold
remains `machine_review_pending`; all reports are `qualification_eligible=false`.

| Artifact | Result | File SHA-256 |
|---|---|---|
| deterministic lifecycle v2 | `passed`; `semanticdeterministic_09c7006509aa34780f409a8b` | `84253336455ccd2e467d5890e657eac8529f4a682d361e37e877158324cd2297` |
| semantic query run | `passed` 15/15 canonical, 14/14 variants; `semanticqueryrun_aecff9f2fb97c6e5c5498e65` | `47b4ed432ce05ee5bd842309f3640df54f40c18686c5b85099b5fee455754025` |
| Context v2 | `passed` 15/15; `semanticcontextoutcome_2930d43da788befb7d83e7a7` | `19dca907f6c94f9e89990dd1ecca13d78e9e9a4a8dded0ce5f2243921793534f` |
| query cost v2 | actual Provider input tokens remain `null` | `13234a16d1d834a87b543307bcb371759b54bc111c15f0d6ff474e3a2a94860d` |

Provider-visible Precision@K is `0.581667`, Recall@K `1.0`, MRR `0.855556`, and nDCG@K
`0.890684`. Target-scoped precision remains a distinct diagnostic at `1.0`. False Suppression and
Duplicate Evidence are both `0.0`. Surface identity, request parameter, receipt explain, and hard
provider-limit failure counts are zero. Local/Provider/MCP byte boundaries are recorded separately;
`25243` is explicitly a UTF-8-bytes/4 estimate and not actual Provider usage.

The first fresh run found a root defect outside metric definitions: a valid snapshot without the
rebuildable `.deeplaw/derived` directory could not open a canonical read snapshot. The minimum fix
uses the explicit `("missing",)` derived identity while continuing to reject a symlink or unsafe
parent. The exact-wheel rerun passed directly against a no-derived baseline snapshot.

The 100k construction run then reproduced a second bounded-context defect: each semantic inventory
froze up to 10,000 globally admitted Knowledge candidates and projected that local inventory into
the Provider-visible finalization packet. The minimum fix resolves at most 256
observation-relevant identity keys, records truncation and the full local digest, and excludes the
full candidate inventory from Provider coverage. Focused contract tests keep the packet under
65,536 bytes with a 10k candidate universe; the affected 100k Statement lane then completed.

## Wiki and Tolaria tasks

The minimum Wiki parity matrix has local focused evidence for Markdown/YAML readability, ownership,
source-grounded update, no-op, multilingual aliases, ambiguity, Wikilinks/backlinks/outlinks,
Relation/navigation separation, evidence drill-down, stable identity across move, reconciliation,
graph defects, rebuild equivalence, bounded Agent Context, and protected evidence. The 1k source
successor run shows an exact incremental/full match for v2/v3 manifests, file inventories, Page
Registry, Link Index, and Resolver.

The exact external Tolaria source report
`tolaria_interop_e9df12e58307d1da94ad995a` passed with self-addressed SHA-256
`ce59e1fa905448c77ce59d938129abb66562de37bffb7383e1afe631caaf921a` and serialized-file SHA-256
`8e0afc232059b6a9c320aeea1f7c6443eedcdbd19582b4b98e9c110354af7460`. Tolaria's own MCP service
opened, read, and updated one allowed multilingual Markdown note. Every protected target was denied
before the external call and retained its hash. `expectedMtime` was only an editor conflict probe;
it did not become a DeepLaw Revision and no canonical Ledger write occurred.

This is source-level interoperability, not a real Tolaria desktop GUI E2E. GUI clicks, desktop
workspace opening, and post-edit DeepLaw reconciliation remain `not_executed`.

## Scale, concurrency, and cache

The claim-ineligible 1k construction report is bound to the earlier exact runtime candidate. File
SHA-256 is `ae96056ac2a5c34699bd7789f64db93cca5cba808f59c76a98abf0b4065a6b8f`;
self-addressed report SHA-256 is
`453886430bb09164dff6bc873b1401aadd2acafb200d6ab4254b369fed4c702f`.

- no failed or degraded operation;
- Wiki page p95 `76.392083 ms`, backlinks p95 `75.547916 ms`, compiled-first p95
  `8.597625 ms`;
- real successor Source Revision with stable canonical source key and changed audit heads;
- exact incremental/full projection equivalence from an empty derived projection;
- warm/fresh cache result and identity hashes match; old Source Revision absent, new present, stale
  cache `false`;
- eight successful concurrent readers;
- provider hard-limit violations `0`.

The dedicated Python 3.13 child-process lane executed 10,000/10,000 bounded read requests with no
failure, unchanged nine-event canonical Ledger, and 8/8 consistent read-only concurrent readers.
Current RSS moved from `99,450,880` to `85,934,080` bytes (`-13.591433%` against the frozen `10%`
growth ceiling); macOS current-RSS sampling does not provide a peak. File SHA-256 is
`a2fbd98fa42960f8fc9efcad9643169a05d53de201d38da75884064c02e8c23a`; self-addressed report
SHA-256 is `a7b6f328d1b1acbb6d79e51a166144e9aad9237836948967f9a806cd0b4fd5e3`.

The public-profile Statement lanes executed at 5,001, 10,000, and 100,000 objects. Every sampled
head/middle/tail target was selected, candidate discovery remained bounded at 512, and maximum
Provider payload was `7,060` bytes against the `65,536`-byte hard limit. Exact final-candidate
5,001/10,000 report file SHA-256 values are
`18d85ee7c90ae87b35d39984b2b5bd993c7ce4e22873079ece9d3e5bd5c26f11` and
`0977588d9fc41d2789325c9acd9b86fa2b3d13770ebd3224cc7aa59521067c76`; self-addressed hashes are
`e4d44aa3380a9206b823a0c4b3a2ab27ca6f3bf41d840b28b948400ec56e9b43` and
`162d44a94d6fafb00a1d7a5dcc0cd40d76cffaf2b3da4d2ec61484154d46467e`.
The affected 100k runtime-evidence report has file SHA-256
`4f280dff9f0f368fe4a4837d1178edfdd30ffeb3b722c1ec0c8a8d25846b102d` and self-addressed SHA-256
`3f71d55647b3981d18e2e2887d477131f1663247e5734b92931ed0be3f532ae4`; it used 400 public
compilation runs, `1,188,124,770` bytes and 310,116 files, with recorded process peak RSS
`1,993,129,984` bytes. These are claim-ineligible construction diagnostics.

Relation/Graph 5,001/10,000/100,000 remain `not_executed`: the public `add_relation` path is
rate-bounded at 120 mutations/minute and no audited bulk Relation constructor exists. No private
SQL/Ledger fallback was introduced. Large Wiki 10k/100k performance also remains `not_executed`.

## Secret and real-Host boundary

Only `.env` metadata was inspected: it is not Git-tracked and is ignored by `.gitignore`. Its
contents were never displayed, sourced, or inherited as a full environment. No Secret value,
Secret hash, private path, or credential was written into a report or artifact.

The approved DeepSeek single-key launcher, Secret canary scan, temporary 0600 credential file,
OpenCode exact host, and Codex isolated-OS-user device login were not run. Therefore this pass does
not claim a completed Secret-isolation or real-Host gate. No current Desktop Codex credential cache
was copied.

## Package artifacts and verification

The evaluated clean source candidate built and clean-installed these local artifacts:

| Artifact | SHA-256 |
|---|---|
| `deeplaw-0.12.0-py3-none-any.whl` | `48afb6e70a4ce8e8e2ce7e6d68b6cb1a9f58cb3a00f7ea401a19df0133bf4e82` |
| `deeplaw-0.12.0.tar.gz` | `ae984bcb430661a409387fe0f840101610da915780327f0477c3ecf13a327d8a` |

The isolated clean install reported `deeplaw 0.12.0`, and the wheel contains the Tolaria report
contract. These are local source-candidate artifacts, not release artifacts. A final CycloneDX
SBOM, license inventory, OpenVEX revalidation, provenance attestation, signature, upload, and public
redownload/hash/install were not executed.

## Validation and open gates

Focused regression, contract, Ruff, and `git diff --check` checks passed for each change. The first
full strict-marker suite collected 1,429 tests: 1,419 passed, four failed only because the one-time
`AGENTS.md` hash had not yet been rebound in repository-visible Development Gold, and six skipped.
After the exact hash re-freeze, the twelve affected evaluation/Gold tests passed. The subsequent
canonical-read and semantic-finalization fixes passed their focused runtime and contract tests.
The frozen final code candidate then passed `uv lock --check`, all 1,427 collected tests with six
skips (`1427 passed, 6 skipped`), full Ruff, and `git diff --check`.

The following release gates are `not_executed` or unresolved:

- repository-external qualification/final holdout and confirmed Human Gold;
- exact Legal Pack qualification;
- Codex ×3 and OpenCode/DeepSeek ×3 real-host tasks with actual Provider token usage;
- 3 OS × Python 3.11/3.12/3.13;
- 5k/10k/100k Relation scale, 10k/100k Wiki performance, and the full release concurrency/cache
  matrix beyond the executed development lanes;
- real Tolaria desktop GUI/open/reconcile and external Git move task;
- final SBOM/license/OpenVEX/provenance/signature/public-redownload chain;
- release-time Tolaria separate-grant/file/contributor confirmation if distributed derived source.

## Final decision

- `release_ready=false`
- `competitive_claim_eligible=false`
- package remains `0.12.0`
- no tag, RC, GA, signature, push, PR expansion, or publication
- source candidate may continue to the missing L2/L3 gates only

“最强 Agent Knowledge OS” remains a product objective, not a verified release or marketing fact.
