<p align="center">
  <a href="README.md">简体中文</a> · <strong>English</strong>
</p>

<h1 align="center">DeepLaw - 2.0</h1>

<p align="center">
  <img src="assets/brand/deeplaw-2-glass.png" width="820" alt="DeepLaw 2.0 frosted transparent-glass wordmark" />
</p>

<p align="center">
  <strong>A verifiable knowledge base for agents.</strong><br />
  Files in. Verifiable knowledge out.
</p>

<p align="center">
  <sub>Architecture 2.0 is the target · The current runnable version is 0.3.0 alpha</sub>
</p>

<p align="center">
  <a href="https://github.com/Eysn0130/DeepLaw/actions/workflows/ci.yml"><img src="https://img.shields.io/github/actions/workflow/status/Eysn0130/DeepLaw/ci.yml?branch=main&style=flat-square&label=CI" alt="CI" /></a>
  <img src="https://img.shields.io/badge/runtime-0.3.0%20alpha-17202A?style=flat-square" alt="Runtime 0.3.0 alpha" />
  <img src="https://img.shields.io/badge/Python-3.11%20%7C%203.13-3776AB?style=flat-square&logo=python&logoColor=white" alt="Python 3.11 and 3.13" />
  <img src="https://img.shields.io/badge/MCP-read--only-18A999?style=flat-square" alt="Read-only MCP" />
  <a href="LICENSE"><img src="https://img.shields.io/badge/license-Apache--2.0-2D3748?style=flat-square" alt="Apache 2.0" /></a>
</p>

<p align="center">
  <a href="#quick-start">Quick Start</a> ·
  <a href="#source-acquisition-and-updates">Sources</a> ·
  <a href="#how-deeplaw-works">How it works</a> ·
  <a href="docs/DEEPLAW_2.md">2.0 Design</a> ·
  <a href="docs/BENCHMARKS.md">Benchmarks</a> ·
  <a href="SECURITY.md">Security</a>
</p>

---

<p align="center">
  <img src="assets/readme/architecture-2-glass.png" width="1180" alt="DeepLaw Architecture 2.0 target: files enter the knowledge base, pass through Locate, Connect, and Explain, and leave as an Evidence Pack for an Agent" />
</p>

The figure above is a target architecture, not a screenshot of the current runtime. The
`0.3.0` baseline turns DOCX and PDF material into a read-only, versioned, traceable Agent
Knowledge Base. It performs bounded location, connection, and verification outside the
model, then delivers a small **Evidence Pack** containing admitted evidence, uncertain
evidence, explicit gaps, and verifiable receipts. Architecture 2.0 adds source-bound
Explain, governed official TXT input, and minimal sufficient evidence selection.

`DeepLaw` is the product name; Architecture 2.0 is the next architecture direction. The
runnable package is still the `0.3.0` alpha baseline. This README keeps implemented
capabilities separate from research targets.

## Quick Start

DeepLaw requires Python 3.11+ and [`uv`](https://docs.astral.sh/uv/):

```bash
git clone https://github.com/Eysn0130/DeepLaw.git
cd DeepLaw
uv tool install .
deeplaw --version
```

`uv tool install .` makes the `deeplaw` executable visible to Agent hosts. Run
`uv sync --extra dev` only for source development, and use `uv tool install --force .`
after upgrading runtime code from a checkout.

Install the bundled DeepLaw team catalog. Before parsing it, the client verifies an Ed25519
detached signature against public keys shipped with the package. It then fetches DOCX/PDF files
from their recorded official URLs, verifies byte sizes and SHA-256 values, and builds locally;
the GitHub repository does not redistribute those source binaries:

```bash
deeplaw official install
deeplaw official status
deeplaw doctor
```

Use `--source-root "/path/to/legal-source-package"` to reuse an already downloaded package
that exactly matches the catalog. Fetch a later monotonically sequenced team catalog with:

```bash
deeplaw official update
```

Transient `429/5xx` and timeout failures use bounded exponential backoff. Sources that already
passed byte-size and SHA-256 verification remain in the local cache, so rerunning can resume the
build. DeepLaw never silently falls back to a non-official mirror just to make an install succeed.
Bundled and HTTPS catalogs fail closed before parsing or building if the signature is missing,
is made by an unknown or revoked key, or does not match the exact catalog bytes.

An in-flight read remains pinned to its release. Later calls from an older MCP process fail
closed after the official epoch changes; restart the Agent/MCP process to use the new release.

The official catalog is optional. `disable` preserves immutable files, `enable` restores the
pointer, and `uninstall` removes only team-catalog releases and caches:

```bash
deeplaw official disable
deeplaw official enable
deeplaw official uninstall
```

User-supplied legal references use a physically separate local library. MCP access remains
read-only; add and delete are explicit local CLI administration:

```bash
deeplaw private add \
  --source "/path/to/user-legal-reference.docx" \
  --confirm-no-case-data
deeplaw private list
deeplaw private search --query "document title article 1"
deeplaw private delete --document-id "doc_..."
```

You can also build an operator-owned source package that you are authorized to process and retain:

```bash
export DEEPLAW_SOURCE_ROOT="/path/to/legal-source-package"
export DEEPLAW_SOURCE_MANIFEST="$DEEPLAW_SOURCE_ROOT/manifest.json"

uv run deeplaw build \
  --source-root "$DEEPLAW_SOURCE_ROOT" \
  --manifest "$DEEPLAW_SOURCE_MANIFEST" \
  --output-root "$HOME/.deeplaw/releases" \
  --activate

uv run deeplaw doctor
uv run deeplaw search --query "刑法第二百六十六条" --as-of 2024-07-01
```

This repository does not distribute restricted legal sources, case material, or generated
SQLite releases. A successful build proves only that machine gates passed; it is not a
human legal approval.

## How DeepLaw Works

<p align="center">
  <img src="assets/readme/knowledge-cycle.png" width="1120" alt="DeepLaw Architecture 2.0 target knowledge cycle: Ingest, Organize, Locate, Connect, Explain, and Verify around the Evidence Core, delivering Evidence, Gaps, and Receipts" />
</p>

Architecture 2.0 defines six core knowledge actions. The table separates the `0.3.0`
baseline from the target:

| Action | Responsibility | Current status and boundary |
| --- | --- | --- |
| **Ingest** | Verify files, extract content, preserve locators and hashes | Official DOCX/PDF; the private library also accepts UTF-8 TXT; processing success is not human approval |
| **Organize** | Build hierarchy, versions, relations, and a Knowledge Map | Heading/article segments and order today; full legal hierarchy is a 2.0 target |
| **Locate** | Find titles, citations, articles, terms, and related segments | Broad terms do not expand into unbounded output |
| **Connect** | Link citations, amendments, repeal, replacement, implementation, and exceptions | Provenance-bound one-hop document relations today; definitions, scope, and challenge closure are 2.0 targets |
| **Explain** | Create source-bound navigation, short summaries, and question decomposition | 2.0 target; today provides excerpts and fixed next questions only |
| **Verify** | Enforce source, time, evidence duties, budgets, gaps, and receipts | Baseline gates exist; witnesses and replay are 2.0 targets |

`Deliver` is the final output action: return at most five evidence cards and fetch the
normalized extracted text of a selected segment on demand. If `truncated=true`, retry with
a higher `max_chars` up to the 6000-character contract limit. Agents never receive the
internal candidate pool. Extracted text still requires comparison with the official source
and locator. Any 2.0 derived explanation must resolve back to an exact source segment
before it can become citable.

## Architecture 2.0 Target: Evidence Core

<p align="center">
  <img src="assets/readme/evidence-core.png" width="1120" alt="DeepLaw Architecture 2.0 target Evidence Core: Sources and Versions, Knowledge Map, Evidence Duties, Limits and Gaps, Receipts and Replay" />
</p>

The Evidence Core is the Architecture 2.0 target. The `0.3.0` baseline already implements
immutable sources, a basic Knowledge Map, Evidence Duties, bounded output, gaps, and
receipts. Coverage witnesses, challenge results, and replay traces are not implemented.

### Sources & Versions

Every admitted card binds to a fixed release, source URL, source hash, segment hash, and
exact locator. Target dates use three distinct states: `verified_in_scope`,
`unverified_metadata`, and `outside_effective_interval`. Unknown temporal metadata does
not enter the verified evidence bucket.

### Knowledge Map

The current release preserves heading/article segments, order, and provenance-bound
one-hop relations. Architecture 2.0 adds a complete legal hierarchy and places model- or
statistics-derived proposals in disposable, rebuildable, release-pinned discovery
sidecars. A sidecar can help Locate or Connect, but it cannot grant authority.

### Evidence Duties

A question is compiled into a closed `QueryPlan`. The current duty set covers primary
rules, exact citations, temporal status, definitions, interpretation, procedure,
counterevidence, and case references. An uncovered required duty becomes a gap; model
memory may not fill it.

### Limits & Gaps

Search returns at most five cards, and navigation or exact lookups normally return fewer.
Character budgets, relation paths, and hop counts are hard limits. DeepLaw separates
admitted evidence, uncertain evidence, out-of-interval candidates, and blocking or
non-blocking gaps.

### Receipts & Replay

A `receipt_id` binds the release, document, segment, source hash, and text hash. `verify`
recomputes the segment hash inside the current immutable release. The 2.0 target adds
coverage witnesses and a compact replay trace explaining selection and rejection.

## Architecture 2.0 Target Capabilities

### Knowledge Release

- Source hashes, provenance declarations, document identity, and versions;
- heading/article segments and order today; full file-to-item hierarchy is a 2.0 target;
- extraction method, configuration, risk, and review status;
- content-addressed publication that never overwrites an existing release ID;
- SQLite `mode=ro&immutable=1` with no runtime corpus-write operation.

### Evidence-first selection

The current runtime already implements QueryPlan, temporal gates, cards, coverage, gaps,
and receipts. Architecture 2.0 changes the selection order:

```text
Question
  → Evidence Duties
  → bounded discovery
  → source / version / extraction admission
  → limitation and counterevidence challenges
  → minimal sufficient evidence set
  → Evidence Pack
```

The target is not “the five highest scores.” It is the smallest evidence set that covers
required duties under a hard context budget.

### Evidence capability types

This is an Architecture 2.0 target. Evidence risk is not compressed into one confidence score. Integrity, source identity,
authority metadata, temporal status, extraction quality, and provenance remain orthogonal.
Only deterministic verification or a human attestation may raise a capability type.

### Challenges before confidence

This is an Architecture 2.0 target. DeepLaw will actively check temporal change, exceptions, definitions, scope, cross-references,
extraction risk, and conflicts. Each challenge is `satisfied`, `unresolved`, or
`not_applicable`; unresolved checks remain gaps.

### Official catalog, user-private references, and private cases

DeepLaw keeps three physical scopes:

| Scope | Storage and mutation | Agent access |
| --- | --- | --- |
| Team-maintained official catalog | Immutable releases under `~/.deeplaw/releases`; monotonic catalog updates; optional disable/uninstall | `search/get/verify/release_info` |
| User-private legal references | Separate `~/.deeplaw/private` root; owner-only files; explicit local add/delete and snapshot rebuild | Explicit `private_*` operations only; never blended with official ranking |
| Analytix case projects | Analytix-owned per-case SQLite/DuckDB, attachments, and sessions | Outside DeepLaw; DeepLaw never reads or owns it |

“Only this user” currently means the local OS account plus owner-only filesystem permissions,
not multi-tenant authentication for a shared server or volume. Case evidence, facts, chats,
identities, transactions, and agent memory remain outside both DeepLaw scopes. The CLI requires
`--confirm-no-case-data`, but that acknowledgement is not content-level DLP.

The full design, invariants, implementation phases, and non-goals are documented in
[`docs/DEEPLAW_2.md`](docs/DEEPLAW_2.md).

## What Is Implemented Today

| Capability | `0.3.0` status |
| --- | --- |
| File processing | Official DOCX/PDF; private UTF-8 TXT; native-text PDF first; local vision consensus for poor pages |
| Official lifecycle | Ed25519 catalog verification, bundled install, HTTPS update, sequence anti-rollback/rewrite checks, enable/disable/uninstall, per-source hash verification |
| Private legal references | Owner-only physical root, explicit add/list/delete, separate immutable snapshot, old-snapshot cleanup on deletion |
| Immutable releases | Source/segment/release hashes, atomic publication, read-only SQLite, receipts |
| Precise location | Titles, aliases, document numbers, articles, and Chinese FTS |
| QueryPlan | Eight closed Evidence Duties, stable plan IDs, and hard bounds |
| Time | Target dates, verified intervals, uncertain metadata, and out-of-interval separation |
| Knowledge Map | Provenance-bound one-hop relations and bounded paths |
| Agent interface | One read-only MCP leaf tool with eight explicit official/private operations and no write operation |
| Hosts | Codex, Claude Code, and OpenCode adapters; Analytix integration is design-only |

Not implemented yet: separate release-approval signatures and an online revocation/supersession feed, a complete bitemporal legal
event ledger, coverage-first selection, coverage witnesses, replay traces, an external
held-out Chinese legal benchmark, and the Analytix pre-schema activation gate.

## One Small Agent Interface

DeepLaw exposes one MCP leaf tool, `law_support`, with eight read-only operations:

| Operation | Purpose |
| --- | --- |
| `search` | Return bounded evidence, uncertain evidence, coverage, relation paths, and gaps |
| `get` | Read normalized extracted text by exact `segment_id`, with explicit `truncated` state |
| `verify` | Verify a receipt and segment hash in the fixed release |
| `release_info` | Inspect the fixed release, schema, review, and redistribution status |
| `private_search` | Search only the user-private legal-reference snapshot |
| `private_get` | Read one exact private segment |
| `private_verify` | Verify a private receipt; old receipts expire after a private snapshot change |
| `private_info` | Inspect the current user-private snapshot |

Private `add` and `delete` exist only in the local administrative CLI. They are not MCP
operations. When both scopes matter, an Agent must query and present them separately; it may
not merge rankings, authority, or temporal conclusions.

Response skeleton:

```json
{
  "release_id": "lawrel_...",
  "query_plan": { "plan_id": "lawplan_...", "obligations": [] },
  "evidence": [{ "segment_id": "seg_...", "receipt_id": "lawrcpt_..." }],
  "uncertain_evidence": [],
  "obligation_coverage": [],
  "gaps": [],
  "total_excerpt_chars": 0
}
```

Stable schemas live in [`contracts`](contracts). The example identifiers above are not
valid runtime identifiers.

## Agent Adapters

| Host | Current entry point | Activation boundary |
| --- | --- | --- |
| Codex | [`plugins/deeplaw`](plugins/deeplaw) | Explicit Skill use plus read-only MCP |
| Claude Code | [`plugins/deeplaw`](plugins/deeplaw) | Skill and MCP in the same plugin |
| OpenCode | [`adapters/opencode`](adapters/opencode) | Default deny; explicit dedicated-agent grant |
| Analytix | [`docs/ANALYTIX_INTEGRATION.md`](docs/ANALYTIX_INTEGRATION.md) | Future turn-scoped integration; no code change yet |

Local Codex install (replace the path with the checkout's absolute path):

```bash
codex plugin marketplace add /absolute/path/to/DeepLaw
codex plugin add deeplaw@deeplaw
```

Installation does not mean automatic invocation. Ordinary code, data, SQL, and document
tasks should not enter DeepLaw. Future Analytix integration must gate legal intent before
provider tool-schema materialization and prove inactive zero impact on routing, the stable
prefix, request bodies, tokens, and latency.

The plugin never downloads or mutates legal data in the background. A user explicitly runs
`deeplaw official install` and later `deeplaw official update` outside the Agent MCP surface.

## File and Extraction Quality

- DOCX: direct OOXML parsing with paragraph order, table rows, and footnote references;
- text PDF: native text layer first;
- poor PDF: page rendering with native/OCR/selected text hashes;
- OCR: local fallback only when the native layer fails;
- human correction: bound to both source-PDF and rendered-page hashes with identity,
  time, role, and visual-comparison attestation;
- disagreement or low confidence: remains `review_required`; the pipeline cannot claim
  `human_reviewed` on its own.

## Benchmarks and Honest Claims

```bash
uv lock --check
uv run ruff check .
uv run pytest
uv run deeplaw eval --cases evals/core-2026-07-14.jsonl --limit 5
git diff --check
```

The current local candidate passes 32/32 known-corpus white-box smoke cases; six cases explicitly
require extraction-risk material to appear only in `uncertain_evidence`, and 109/109 returned
receipts complete a round-trip verification. This result is pinned to one release,
database, case set, source tree, and environment. It is not a blind test, a held-out set, a
human legal gold standard, or a cross-system leaderboard. See
[`docs/BENCHMARKS.md`](docs/BENCHMARKS.md) for hashes and limitations.

DeepLaw will publish a leadership claim only after external held-out evaluation, an expert
Chinese legal set, mutation tests, and non-legal activation tests all pass. Ambitious
architecture is welcome; performance claims require evidence.

## Source and Safety Boundaries

- DeepLaw returns research evidence candidates, not legal advice, factual findings, or a verdict;
- matching a date does not prove that a rule applies to a case;
- live web content never enters primary evidence at query time;
- a model may not determine amendment, repeal, conflict, or priority on its own;
- DeepLaw does not predict guilt, sentence, liability, or case outcome;
- the private library accepts legal references, not case evidence, facts, chats, or attachments;
- private material never changes official releases, review status, ranking, or update state;
- restricted sources and case data must not appear in issues, PRs, logs, screenshots, or benchmarks.

See [`docs/CORPUS_GOVERNANCE.md`](docs/CORPUS_GOVERNANCE.md) and
[`SECURITY.md`](SECURITY.md).

## Documentation

| Document | Scope |
| --- | --- |
| [`docs/DEEPLAW_2.md`](docs/DEEPLAW_2.md) | Complete 2.0 design and research gates |
| [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md) | Current `0.3.0` implementation and runtime facts |
| [`docs/CORPUS_GOVERNANCE.md`](docs/CORPUS_GOVERNANCE.md) | Source, review, license, release, and update governance |
| [`docs/BENCHMARKS.md`](docs/BENCHMARKS.md) | Reproducible smoke evidence and the next evaluation protocol |
| [`docs/AGENT_ADAPTERS.md`](docs/AGENT_ADAPTERS.md) | Codex, Claude Code, and OpenCode adapters |
| [`docs/ANALYTIX_INTEGRATION.md`](docs/ANALYTIX_INTEGRATION.md) | Future Analytix integration and zero-impact gates |
| [`docs/SOURCE_AUDIT_2026-07-14.md`](docs/SOURCE_AUDIT_2026-07-14.md) | Hash-bound AI precheck for the current restricted source package |

## Roadmap

- [x] Immutable Knowledge Releases, receipts, and read-only MCP
- [x] QueryPlan, Evidence Duties, temporal buckets, and explicit gaps
- [x] Provenance-bound Knowledge Map with bounded one-hop navigation
- [x] Page-level PDF evidence and human-review attestations
- [x] Codex, Claude Code, and OpenCode adapters
- [x] Team catalog install/update/enable/disable/uninstall lifecycle
- [x] Ed25519 detached team-catalog signatures, packaged public trust roots, and mandatory verification
- [x] Physically separate user-private legal references with explicit add/delete and read-only Agent access
- [ ] Coverage witnesses, challenge results, and replay traces
- [ ] Coverage-first minimal sufficient evidence selection
- [ ] Official TXT input, complete legal hierarchy, and source-bound Explain (private UTF-8 TXT is implemented)
- [ ] Corpus Coverage Manifest and bitemporal legal event ledger
- [ ] Separate release-approval signatures, online revocation, supersession feed, and complete secure-update metadata
- [ ] External held-out Chinese legal evidence benchmark
- [ ] Analytix turn-scoped activation and inactive zero-impact gate

## Source Acquisition and Updates

DeepLaw is being built as a general legal knowledge base. The tables below show only the
material currently recorded as of **2026-07-14**: **28** binary source files — **10 DOCX**,
**18 PDF**, and **0 HTML**. They do not limit future coverage. Counts come from the
bundled team catalog; this repository distributes only catalog metadata, detached signatures,
public trust roots, source URLs, sizes, and hashes, not the source binaries. Running
`deeplaw official update` fetches later signed monotonic catalogs.

| Source-package group | Count | Coverage |
| --- | ---: | --- |
| Core legal sources | 4 | Criminal Law, Criminal Procedure Law, amendments, and filing standards |
| Finance and illegal fundraising | 4 | Money laundering, AML, illegal fundraising, and prohibition rules |
| Data and cyber | 3 | Personal information, data security, and telecom-fraud rules |
| Case references | 4 | Public cases from the People's Court Case Library |
| Procedure and evidence | 4 | Economic-crime procedure, criminal procedure, and asset handling |
| AML, payments, and beneficial ownership | 8 | Foreign exchange, beneficial ownership, due diligence, and payments |
| Offence topic | 1 | Judicial interpretation on tax-administration crimes |
| **Total** | **28** | **10 DOCX + 18 PDF** |

DeepLaw records the **issuing authority** separately from the **official download host**:
the former determines authority; the latter records where the source binary was obtained.

| Official download source | Count | Files currently obtained |
| --- | ---: | --- |
| [National Laws and Regulations Database](https://flk.npc.gov.cn/) | 10 | DOCX: laws, amendments, and judicial interpretations |
| [Ministry of Justice Administrative Regulations Database](https://xzfg.moj.gov.cn/) | 4 | PDF: administrative regulations and related rules |
| [People's Bank of China](https://www.pbc.gov.cn/) and its official branch site | 6 | PDF: AML, payments, due diligence, and amendment decisions |
| [Shandong Court](https://www.sdcourt.gov.cn/) official hosts | 5 | PDF: case-library references and procedure material |
| Official hosts of the [CSRC](https://www.csrc.gov.cn/), [NIA](https://www.nia.gov.cn/), and [SZSE](https://www.szse.cn/) | 3 | PDF: officially hosted originals issued by the relevant authority |
| **Total** | **28** | **Each file records URL, format, byte size, and SHA-256** |

“Team-maintained official catalog” means that the DeepLaw team maintains catalog identity,
source declarations, hashes, and version lifecycle. It does not mean that every page has
completed human legal review. In the reproducible build of the current 28 items, **5 PDFs
and 15 pages** still carry extraction-review flags. Their segments are returned only as
`uncertain_evidence`, never as admitted primary evidence. Full page-level records remain in
the local release's `build-report.json`; corrections require a later catalog sequence and a
new immutable release rather than an overwrite.

Concise update method:

1. Identify the title, document number, issuing authority, promulgation/effective date, and status.
2. Download a DOCX/PDF original supported by the current package from the issuing authority or an official host; do not guess download URLs or save web pages as originals.
3. Validate format, first-page identity, and SHA-256; update the manifest with the URL, hash, version relationship, and notes before building a new immutable release.

Drafts, consultation papers, web-page-only material, commercial reposts, and private case
material do not enter the public source package. Cases are research and argument references,
not substitutes for the legal effect of statutory sources.

## Community and License

Reproducible location, version, extraction, and safety issues are welcome when they use
synthetic fixtures. See [`CONTRIBUTING.md`](CONTRIBUTING.md),
[`CODE_OF_CONDUCT.md`](CODE_OF_CONDUCT.md), and [`SECURITY.md`](SECURITY.md).

DeepLaw source code is licensed under the [Apache License 2.0](LICENSE). That license does
not automatically grant redistribution rights for external legal sources, cases, site
layouts, third-party trademarks, models, or tools. Brand images were generated with image2
and reviewed for use in this project. The DeepLaw name still requires professional
trademark review before commercial release in a target jurisdiction.
