<p align="center">
  <a href="README.md">简体中文</a> · <strong>English</strong>
</p>

<h1 align="center">DeepLaw 2.0</h1>

<p align="center">
  <img src="assets/brand/deeplaw-2-glass.png" width="820" alt="DeepLaw 2.0 wordmark" />
</p>

<p align="center">
  <strong>A verifiable knowledge base for agents.</strong><br />
  Turn files into knowledge agents can locate, verify, and replay.
</p>

<p align="center">
  <a href="https://github.com/Eysn0130/DeepLaw/actions/workflows/ci.yml"><img src="https://img.shields.io/github/actions/workflow/status/Eysn0130/DeepLaw/ci.yml?branch=main&style=flat-square&label=CI" alt="CI" /></a>
  <img src="https://img.shields.io/badge/version-v0.3.0-17202A?style=flat-square" alt="Version v0.3.0" />
  <img src="https://img.shields.io/badge/Python-3.11%E2%80%933.13-3776AB?style=flat-square&logo=python&logoColor=white" alt="Python 3.11 through 3.13" />
  <img src="https://img.shields.io/badge/MCP-read--only-18A999?style=flat-square" alt="Read-only MCP" />
  <a href="LICENSE"><img src="https://img.shields.io/badge/license-Apache--2.0-2D3748?style=flat-square" alt="Apache 2.0" /></a>
</p>

<p align="center">
  <a href="#quick-start">Quick Start</a> ·
  <a href="#deeplaw-architecture">Architecture</a> ·
  <a href="#evidence-compiler">Evidence Compiler</a> ·
  <a href="#agent-integrations">Agent Integrations</a> ·
  <a href="#current-catalog-and-updates">Current Catalog</a> ·
  <a href="#documentation">Documentation</a>
</p>

---

<p align="center">
  <img src="assets/readme/product-flow-glass.png" width="1180" alt="Files enter the DeepLaw 2.0 knowledge base, pass through Locate, Connect, and Explain, and leave as an Evidence Pack for an Agent" />
</p>

DeepLaw 2.0 turns legal materials into versioned, traceable knowledge releases. Before
answering a question, it identifies the evidence required and selects a compact set under
source, version, temporal, and extraction-quality gates. Uncovered requirements return as
explicit gaps instead of being left for the model to guess.

It does not put an entire knowledge base into the context window, and it does not let one
opaque score decide an answer. Candidate discovery, relationship traversal, and quality
admission happen outside the model context. The Agent receives only a bounded,
ID-addressable **Evidence Pack**.

## Core Capabilities

| Capability | How DeepLaw 2.0 handles it |
| --- | --- |
| **Source binding** | Every citable segment binds to the original file, official source, version, locator, source hash, and segment hash |
| **Structural fidelity** | Document order, heading/article hierarchy, pages, paragraphs, table rows, and extraction evidence are retained |
| **Topic isolation** | Reviewed concepts bind source hashes and articles; neighboring offences or thresholds cannot substitute, and unresolved topics become gaps |
| **Evidence compilation** | Required evidence duties are defined first, then covered by a compact, de-duplicated set selected under a hard budget and explicit priorities |
| **Limits first** | Unknown dates, extraction risk, exceptions, and conflicts become uncertain evidence or gaps instead of being hidden by a relevance score |
| **Bounded delivery** | Search returns at most five cards by default; full text is fetched by exact `segment_id`, never by exposing the internal candidate pool |
| **Verifiable receipts** | A receipt binds to an immutable release, document, segment, and text hash for independent verification |
| **Host isolation** | The official catalog, user-private legal references, and Analytix case projects remain physically separate; the Agent interface stays read-only |

## DeepLaw Architecture

The DeepLaw architecture separates original files from views designed for Agent use:

```text
Immutable Source Files
  → Document IR
  → Immutable SQLite Knowledge Release
  → Rebuildable Markdown / Search / Map Views
  → Evidence Compiler
  → Evidence Pack
  → Agent
```

- **Immutable source files** retain acquisition provenance, file identity, byte size, and
  SHA-256 as the starting point for traceability.
- **Document IR** normalizes DOCX, PDF, and TXT inputs into ordered blocks with
  locators, layout, and extraction-quality evidence without prematurely discarding pages,
  paragraphs, tables, or source relationships.
- **SQLite Knowledge Release** is the canonical runtime store. It is opened with
  `mode=ro&immutable=1` and retains blocks, segments, versions, relationships, risk, and hashes.
- **Markdown derived views** support human browsing, correction, and review. They are
  generated deterministically from Document IR, may be deleted and rebuilt, and never
  replace the original file or the canonical runtime store.
- **Discovery views** such as full-text indexes, relationship maps, and other candidate
  finders are pinned to a release and rebuildable. They cannot raise authority, temporal,
  or review status.

This separation avoids treating Markdown as a database and avoids creating an extracted,
flattened copy that can no longer resolve back to the original file.

### The knowledge cycle

<p align="center">
  <img src="assets/readme/knowledge-cycle.png" width="1120" alt="The DeepLaw 2.0 Ingest, Organize, Locate, Connect, Explain, and Verify knowledge cycle" />
</p>

| Action | Responsibility | Constraint |
| --- | --- | --- |
| **Ingest** | Verify files, extract content, and build Document IR | Processing success is not human approval |
| **Organize** | Retain hierarchy, order, versions, and the Knowledge Map | A derived summary cannot overwrite source text |
| **Locate** | Find titles, document numbers, articles, terms, and related segments | Broad terms do not expand into unbounded candidates |
| **Connect** | Link citations, amendments, repeal, replacement, definitions, and exceptions | A relationship is not itself a legal conclusion |
| **Explain** | Produce source-bound navigation, short summaries, and question decomposition | A derived explanation must resolve to an exact source segment |
| **Verify** | Check source, time, evidence duties, budgets, gaps, and receipts | The system does not invent content to look complete |

`Deliver` is the final action: only the evidence, limitations, gaps, and receipts needed for
the task reach the Agent.

### Evidence Core

<p align="center">
  <img src="assets/readme/evidence-core.png" width="1120" alt="The Evidence Core contains Sources and Versions, a Knowledge Map, Evidence Duties, Limits and Gaps, and Receipts and Replay" />
</p>

The Evidence Core keeps five kinds of information on one verifiable chain:

- **Sources & Versions** pin the release, source URL, source hash, segment hash, and exact locator.
- **Knowledge Map** admits source-bound relationships to the authority path; derived
  relationships can only propose candidates.
- **Evidence Duties** compile a question into a closed set of requirements covering the
  primary rule, exact citation, temporal status, definitions, interpretation, procedure,
  amount or filing thresholds, counterevidence, and case references.
- **Limits & Gaps** bound cards, characters, relationship paths, and hops while separating
  evidence, corpus, review, temporal, and extraction gaps.
- **Receipts & Replay** bind selection to a release, segment, and hashes so results can be
  verified and replayed.

## Evidence Compiler

The Evidence Compiler is the core query path in DeepLaw 2.0. It does not simply truncate a
list of top-scoring segments. It first defines what would be sufficient for the current
question, then selects content:

```text
Question
  → closed Evidence Duties
  → bounded candidate discovery
  → integrity / relevance / temporal-intent / extraction admission
  → coverage witnesses
  → limitation and counterevidence challenges
  → bounded coverage-first evidence set
  → evidence + uncertain evidence + gaps + receipts
```

Within a bounded candidate pool and context budget, the compiler follows deterministic
priorities: exact targets and required duties first, then definitions, limits, exceptions,
counterevidence, and version changes. A candidate is selected only when it adds or improves
a witness. This is a bounded, coverage-first de-duplication process, not a claim of a
globally minimal set. A flood of topically similar segments cannot displace an exact article
or a necessary limitation. A candidate that does not pass capability predicates cannot
produce a coverage witness or mark a required duty as covered.

An Evidence Pack separates:

| Output | Meaning |
| --- | --- |
| `evidence` | Research evidence that passed integrity, relevance, and the temporal/extraction gates activated for this query; it is not a claim of human-reviewed source identity or current legal effect |
| `uncertain_evidence` | Relevant material with at least one unmet admission condition |
| `obligation_coverage` | The machine-checkable witnesses covering each evidence duty |
| `gaps` | Uncovered or unresolved evidence, corpus, temporal, review, and extraction requirements |
| `receipt_id` | A receipt whose segment hash can be recomputed inside the fixed release |

Discovery may combine title, article, relevance, and source tier to order candidates, but
that ordering score cannot raise integrity, temporal, extraction, or human-review status.
Models and derived indexes may help discover candidates; they cannot determine amendment
or repeal, erase a blocking gap, or turn a research candidate into a case-applicability conclusion.

## Current Version v0.3.0

| Capability | Current status |
| --- | --- |
| File processing | The official catalog accepts DOCX/PDF; the private library also accepts UTF-8 TXT; block-level locators and extraction evidence are retained |
| Data model | Immutable originals, Document IR, read-only SQLite releases, and rebuildable Markdown derived views remain separate |
| Official catalog | Ed25519 verification, HTTPS updates, sequence anti-rollback/rewrite checks, and per-source byte-size and SHA-256 verification |
| User-private library | Owner-only storage, explicit add/delete, separate immutable snapshots, and no blending with official results |
| Locate and Connect | Titles, aliases, document numbers, articles, Chinese full-text search, source-bound topic locators, and bounded source-bearing relationship paths |
| Evidence delivery | Closed QueryPlan, heuristic Evidence Duties, query-activated temporal/extraction gates, bounded evidence, explicit gaps, and receipts |
| Agent interface | One read-only MCP leaf tool with separate official and private operations and no corpus-write operation |
| Hosts | Codex, Claude Code, and OpenCode adapters; Analytix case projects remain outside DeepLaw 2.0 |

## Quick Start

DeepLaw 2.0 requires Python 3.11+ and [`uv`](https://docs.astral.sh/uv/):

```bash
git clone https://github.com/Eysn0130/DeepLaw.git
cd DeepLaw
uv tool install '.[document-engine]'
deeplaw --version
```

The official catalog includes PDFs. Before the first official install or any update, also
install PDF rendering, OCR, and Simplified Chinese language data:

```bash
# macOS (Homebrew)
brew install poppler tesseract tesseract-lang

# Debian / Ubuntu
sudo apt-get update
sudo apt-get install -y poppler-utils tesseract-ocr tesseract-ocr-chi-sim
```

Verify all four build dependencies independently:

```bash
deeplaw-document-engine --version
pdftoppm -v
tesseract --version
tesseract --list-langs | grep -x 'chi_sim'
```

A signed official catalog's build policy is mandatory. `official install` and
`official update` run the same strict preflight before downloading any official source,
building a release, or changing the active release. A missing dependency aborts the
operation without silent degradation, and the CLI cannot weaken the catalog policy. A
machine that only reads an existing release may use the lightweight `uv tool install .`
instead. Select the document engine explicitly for a risky user PDF:

```bash
uv tool install --force '.[document-engine]'
deeplaw private add \
  --source "/path/to/scanned-legal-reference.pdf" \
  --pdf-fallback document-engine \
  --allow-needs-ocr \
  --confirm-no-case-data
```

Install the team-maintained official catalog. The client verifies the signature, downloads
original files from cataloged official sources, and builds an immutable release locally.
The repository does not redistribute those source files.

```bash
deeplaw official install
deeplaw official status
deeplaw doctor
```

For human browsing or review, export deterministic Markdown from the immutable release.
The output is a disposable derived view and can always be rebuilt:

```bash
deeplaw export-markdown --output "/path/to/deeplaw-markdown"
```

When the team publishes a new catalog, the user updates explicitly:

```bash
deeplaw official update
```

An existing source package that exactly matches the catalog can be reused:

```bash
deeplaw official install --source-root "/path/to/legal-source-package"
```

The official catalog is optional. Disabling or uninstalling it does not touch the private
library:

```bash
deeplaw official disable
deeplaw official enable
deeplaw official uninstall
```

User-owned legal references enter a separate local private library. Import requires an
explicit confirmation that the file is not case material. Agents can read the library but
cannot upload or delete through MCP.

```bash
deeplaw private add \
  --source "/path/to/user-legal-reference.docx" \
  --confirm-no-case-data
deeplaw private list
deeplaw private search --query "document title article one"
deeplaw private delete --document-id "doc_..."
```

## Agent Integrations

| Host | Entry point | Activation boundary |
| --- | --- | --- |
| Codex | [`plugins/deeplaw`](plugins/deeplaw) | A Skill explicitly invokes the read-only MCP for legal tasks |
| Claude Code | [`plugins/deeplaw`](plugins/deeplaw) | Uses the same Skill and MCP contract |
| OpenCode | [`adapters/opencode`](adapters/opencode) | Denied by default and explicitly enabled for a dedicated agent |
| Analytix | [`docs/ANALYTIX_INTEGRATION.md`](docs/ANALYTIX_INTEGRATION.md) | Future turn-scoped integration; its case-project library is not part of DeepLaw 2.0 |

Install locally in Codex:

```bash
codex plugin marketplace add /absolute/path/to/DeepLaw
codex plugin add deeplaw@deeplaw
```

DeepLaw 2.0 exposes one MCP leaf tool: `law_support`. The official catalog uses
`search/get/verify/release_info`; the private library uses
`private_search/private_get/private_verify/private_info`. All eight operations are
read-only. Installing the plugin never downloads or mutates data in the background;
installation and updates require an explicit CLI command.

Ordinary code, data, SQL, and document tasks should not activate DeepLaw 2.0. A host should
gate legal intent before adding the tool schema to model context, protecting the general
Agent from unnecessary token, latency, and routing costs.

## Knowledge Boundaries

| Scope | Storage and updates | Agent access |
| --- | --- | --- |
| Team-maintained official catalog | Catalog state under `~/.deeplaw/official`; releases under `~/.deeplaw/releases`; monotonically sequenced signed updates | Four official read-only operations; users may disable or uninstall it |
| User-private legal references | Owner-only `~/.deeplaw/private`; explicit local add/delete and independent snapshot rebuilds | Explicit `private_*` operations only; never changes official source identity, ranking, or updates |
| Analytix case projects | Analytix-owned per-case SQLite/DuckDB, attachments, and sessions | Outside DeepLaw 2.0 and never read by DeepLaw 2.0 |

The local private library relies on the operating-system account and owner-only file
permissions; it is not multi-tenant authentication for a shared service. Case evidence,
facts, chats, identities, transactions, and Agent memory must not enter either the official
catalog or the user-private legal-reference library.

## File Processing and Quality Gates

- **DOCX** is parsed directly from OOXML while retaining paragraphs, table rows, styles,
  and footnote references.
- **PDF** retains native text, layout blocks, locators, extraction methods, confidence
  information, and risk flags per page; poor pages enter multi-path parsing and visual review.
- **TXT** uses strict UTF-8 decoding with stable line and paragraph order.
- **Document IR** assigns every block a stable ID, order, text hash, page or paragraph,
  kind, source, and quality state.
- **Markdown** is a derived view generated from IR for browsing and correction. It is not
  the source of truth for segmentation, retrieval, or legal citation.

Quality decisions are attached to pages and segments instead of contaminating an entire
document. A segment that has not passed extraction admission appears only in
`uncertain_evidence`, never as verified primary evidence. Detailed page-level status,
methods, hashes, and audit records remain in the release build report. Corrections are
published through a new immutable release.

## Quality and Verification

```bash
uv lock --check
uv run ruff check .
uv run pytest
uv run deeplaw eval --cases evals/core-2026-07-14.jsonl --limit 5
git diff --check
```

The reproducible smoke suite covers exact location, temporal buckets, extraction admission,
official/private isolation, and receipt round-trip verification. The pinned release,
database, cases, source tree, environment, hashes, and metrics are recorded in
[`docs/BENCHMARKS.md`](docs/BENCHMARKS.md). Cross-system performance claims require external
held-out evaluation under the same corpus, questions, model, and context budget.

## Safety and Responsibility

- DeepLaw 2.0 returns verifiable research evidence; it does not replace legal advice,
  factual findings, or adjudication.
- Live web content never enters primary evidence at query time, and a model cannot decide
  amendment, repeal, conflict, or priority on its own.
- User-private material cannot change an official release, review status, ranking, or update lifecycle.
- Restricted legal sources and case information must not appear in issues, pull requests,
  logs, screenshots, or public benchmarks.

See [`docs/CORPUS_GOVERNANCE.md`](docs/CORPUS_GOVERNANCE.md) for corpus governance and
[`SECURITY.md`](SECURITY.md) for security reporting.

## Roadmap

- [x] Immutable Knowledge Releases, Document IR, receipts, and read-only MCP
- [x] Signed official-catalog lifecycle and physically separate user-private legal references
- [x] Precise location, evidence duties, temporal/extraction gates, and explicit gaps
- [x] Codex, Claude Code, and OpenCode adapters
- [ ] Extend the complete legal hierarchy and bitemporal legal-event ledger
- [ ] Add a Corpus Coverage Manifest and release approval/revocation metadata
- [ ] Establish an external held-out Chinese legal-evidence benchmark
- [ ] Complete Analytix turn-scoped activation and the inactive zero-impact A/B gate

## Documentation

| Document | Scope |
| --- | --- |
| [`docs/DEEPLAW_2.md`](docs/DEEPLAW_2.md) | DeepLaw 2.0 technical design, formal invariants, and research gates |
| [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md) | System architecture, storage, and runtime facts |
| [`docs/DOCUMENT_IR.md`](docs/DOCUMENT_IR.md) | DOCX/PDF/TXT ingestion, Document IR, multi-candidate PDF gates, and Markdown's role |
| [`docs/CORPUS_GOVERNANCE.md`](docs/CORPUS_GOVERNANCE.md) | Source, review, licensing, release, and update governance |
| [`docs/BENCHMARKS.md`](docs/BENCHMARKS.md) | Reproducible validation results and the next evaluation protocol |
| [`docs/RESEARCH_MATRIX.md`](docs/RESEARCH_MATRIX.md) | Agent knowledge-base research matrix, layer boundaries, and comparison gates |
| [`docs/AGENT_ADAPTERS.md`](docs/AGENT_ADAPTERS.md) | Codex, Claude Code, and OpenCode adapters |
| [`docs/ANALYTIX_INTEGRATION.md`](docs/ANALYTIX_INTEGRATION.md) | Future Analytix integration and zero-impact gates |
| [`docs/SOURCE_AUDIT_2026-07-14.md`](docs/SOURCE_AUDIT_2026-07-14.md) | Source and build-history audit for the first 28 materials |

## Current Catalog and Updates

DeepLaw 2.0 is a general-purpose legal knowledge base. As of **2026-07-14**, the current
official catalog records **28** items: **10 DOCX** and **18 PDF**. This is the present
coverage, not a limit on future jurisdictions or material types. The repository distributes
the signed catalog, public trust roots, source URLs, sizes, and hashes; original files are
acquired from official sources during installation.

| Current source group | Count | Coverage |
| --- | ---: | --- |
| Core legal sources | 4 | Criminal Law, Criminal Procedure Law, amendments, and filing standards |
| Finance and illegal fundraising | 4 | Money laundering, AML, illegal fundraising, and prohibition rules |
| Data and cyber | 3 | Personal information, data security, and telecom-fraud rules |
| Case references | 4 | Public cases from the People's Court Case Library |
| Procedure and evidence | 4 | Economic-crime procedure, criminal procedure, and asset handling |
| AML, payments, and beneficial ownership | 8 | Foreign exchange, beneficial ownership, due diligence, and payments |
| Offence topic | 1 | Judicial interpretation on tax-administration crimes |
| **Total** | **28** | **10 DOCX + 18 PDF** |

DeepLaw 2.0 records the **issuing authority** separately from the **official download host**.
The former identifies source authority; the latter records where the original file was
obtained.

Here, “official catalog” means a DeepLaw-team-maintained and signed download catalog whose
materials come from the official sites below. It is not certification of DeepLaw's build by
an issuing authority, nor does it mean every legal proposition has received human review.

| Official download source | Count | Files currently obtained |
| --- | ---: | --- |
| [National Laws and Regulations Database](https://flk.npc.gov.cn/) | 10 | DOCX: laws, amendments, and judicial interpretations |
| [Ministry of Justice Administrative Regulations Database](https://xzfg.moj.gov.cn/) | 4 | PDF: administrative regulations and related rules |
| [People's Bank of China](https://www.pbc.gov.cn/) and its official branch site | 6 | PDF: AML, payments, due diligence, and amendment decisions |
| [Shandong Court](https://www.sdcourt.gov.cn/) official hosts | 5 | PDF: case-library references and procedure material |
| Official hosts of the [CSRC](https://www.csrc.gov.cn/), [NIA](https://www.nia.gov.cn/), and [SZSE](https://www.szse.cn/) | 3 | PDF: officially hosted originals issued by the relevant authority |
| **Total** | **28** | **Each file records URL, format, byte size, and SHA-256** |

Users fetch a team update explicitly:

```bash
deeplaw official update
```

The team maintains the catalog in three steps:

1. Identify the title, document number, issuing authority, promulgation/effective dates,
   and legal status.
2. Obtain the original from the issuing authority or an official download host without
   guessing URLs or saving webpage text as an original file.
3. Verify format, first-page identity, size, and SHA-256; record version relationships and
   build a new immutable release.

Drafts, consultation papers, webpage-only materials, commercial-database reposts, and
private case material do not enter the public catalog. Cases support research and argument;
they do not replace legal-effect analysis of normative sources.

## Community and License

Reproducible location, version, extraction, and security reports are welcome when they use
synthetic fixtures. See [`CONTRIBUTING.md`](CONTRIBUTING.md),
[`CODE_OF_CONDUCT.md`](CODE_OF_CONDUCT.md), and [`SECURITY.md`](SECURITY.md).

DeepLaw source code is available under the [Apache License 2.0](LICENSE). Rights in external
legal sources, cases, website layouts, and third-party assets remain with their respective
owners. See [`THIRD_PARTY_NOTICES.md`](THIRD_PARTY_NOTICES.md) for licenses, model terms,
and redistribution boundaries of optional document-processing dependencies.
