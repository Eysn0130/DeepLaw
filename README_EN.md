<p align="center">
  <a href="README.md">简体中文</a> · <strong>English</strong>
</p>

<h1 align="center">DeepLaw 2.0</h1>

<p align="center">
  <img src="assets/brand/deeplaw-2-glass.png" width="760" alt="DeepLaw 2.0 wordmark" />
</p>

<p align="center">
  <strong>Give agents knowledge with sources, state, and boundaries.</strong><br />
  Local single-user Agent Knowledge OS · Source-bound · Human-reviewed · Capsule-delivered
</p>

<p align="center">
  <a href="https://github.com/Eysn0130/DeepLaw/actions/workflows/ci.yml"><img src="https://img.shields.io/github/actions/workflow/status/Eysn0130/DeepLaw/ci.yml?branch=main&style=flat-square&label=CI" alt="CI" /></a>
  <a href="https://github.com/Eysn0130/DeepLaw/releases/tag/v0.7.0"><img src="https://img.shields.io/badge/release-v0.7.0-17202A?style=flat-square" alt="Release v0.7.0" /></a>
  <img src="https://img.shields.io/badge/Python-3.11%E2%80%933.13-3776AB?style=flat-square&logo=python&logoColor=white" alt="Python 3.11 through 3.13" />
  <img src="https://img.shields.io/badge/local--first-owner--controlled-36CDBB?style=flat-square" alt="Local-first and owner-controlled" />
  <img src="https://img.shields.io/badge/MCP-read--only-18A999?style=flat-square" alt="Read-only MCP" />
  <a href="LICENSE"><img src="https://img.shields.io/badge/license-Apache--2.0-2D3748?style=flat-square" alt="Apache 2.0" /></a>
</p>

<p align="center">
  <a href="#why-agents-need-a-knowledge-system">Why</a> ·
  <a href="#five-step-start">Start</a> ·
  <a href="#from-sources-to-a-capsule">How it works</a> ·
  <a href="#connect-an-agent">Agent access</a> ·
  <a href="#v070-at-a-glance">Capabilities</a> ·
  <a href="#open-source-collaboration">Contribute</a> ·
  <a href="#documentation">Docs</a>
</p>

<p align="center">
  <img src="assets/readme/agent-knowledge-flow-v0.7.png" width="1180" alt="Documents, code, and structured data enter a local Knowledge Vault, pass through Review, Recall, and Explain, and reach an Agent as a bounded Knowledge Capsule" />
</p>

<p align="center">
  <sub>Local sources enter the Vault; reviewed, explainable recall becomes a bounded Knowledge Capsule for the Agent.</sub>
</p>

DeepLaw sits between local material and an Agent. It compiles documents, code, decisions,
constraints, experience, and tool results into **traceable Knowledge Assets**, then delivers a
small, sufficient, reviewable **Knowledge Capsule** for the task at hand.

It is not a growing transcript and does not pour raw vector-search hits into a prompt. Sources,
knowledge, review, retrieval, and feedback have separate identities and lifecycles. The owner's
SQLite database, content-addressed source fragments, and append-only audit chain are the canonical
local state.

## Why agents need a knowledge system

Agents can reason and act, but they do not inherently know which local statement is still current,
which one was reviewed, why it belongs in this task, or what evidence is missing. A useful Agent
knowledge base must answer five questions:

| Question | DeepLaw's answer |
| --- | --- |
| **Where did this knowledge come from?** | Exact Source Revision, fragment, locator, and hash; a summary never replaces the source |
| **May it be used now?** | `proposed / quarantined / active / superseded / revoked` lifecycle plus a human Review Receipt |
| **Why was it recalled?** | Hashable Query Plan, channel candidates, admission/exclusion reasons, and Explain Trace |
| **How much should reach the Agent?** | A Knowledge Capsule bounded across text, source metadata, and complete serialized payload |
| **How does Agent feedback return?** | Capsule-bound Run Record / feedback artifact → isolated Proposal Inbox → human review; never a direct write to active knowledge |

That turns a repository that can find chunks into a system that can deliver verifiable context.

## Five-step start

DeepLaw requires Python 3.11+ and [`uv`](https://docs.astral.sh/uv/). Install the signed wheel from
GitHub Release:

```bash
uv tool install https://github.com/Eysn0130/DeepLaw/releases/download/v0.7.0/deeplaw-0.7.0-py3-none-any.whl
```

Run the complete local loop:

```bash
# 1. Initialize an owner-controlled Vault
deeplaw init ./vault --name my-project

# 2. Ingest a file or directory; this creates proposed / quarantined knowledge only
deeplaw add ./docs --vault ./vault --confirm-no-case-data

# 3. Review, edit, split, merge, or reject proposals locally
deeplaw review --vault ./vault --interactive

# 4. Build a Query Plan, Explain Trace, and bounded Capsule for this task
deeplaw recall "Which constraints govern this release?" \
  --vault ./vault --confirm-no-case-data --output capsule.json

# 5. Inspect selection, exclusion, source coverage, and gaps
deeplaw explain --vault ./vault --last
```

The default path needs no remote database, background service, or model API key. `recall` verifies
the Capsule in the same result and fails closed instead of handing unverifiable context to an Agent.

## From sources to a Capsule

<p align="center">
  <img src="assets/readme/agent-knowledge-cycle-v0.7.png" width="1080" alt="A local Knowledge Vault coordinates Ingest, Review, Recall, Explain, Verify, and Deliver while retaining Sources, Gaps, and Receipts" />
</p>

<p align="center">
  <sub>Knowledge is not a one-time index: sources, review, recall, explanation, verification, and delivery form a reviewable lifecycle.</sub>
</p>

```mermaid
flowchart LR
  S["Local sources"] --> IR["Source Adapters · IR · Tree"]
  IR --> P["Source-bound proposals"]
  P --> R{"Human review"}
  R -->|approve| K["Active Knowledge Assets"]
  R -->|reject / revise| P
  K --> Q["Query Plan · Retrieval · Admission"]
  Q --> C["Verified Knowledge Capsule"]
  C --> A["Agent via read-only MCP"]
  A -. "Run Record / feedback artifact" .-> I["Isolated Proposal Inbox"]
  I -. "operator review only" .-> R
```

### Five core objects

| Object | Role |
| --- | --- |
| **Source Revision** | Immutable source bytes, structure, order, locator, and hash, preserved independently of derived knowledge |
| **Knowledge Asset** | A constraint, decision, procedure, experience, concept, or question supported by one or more source fragments |
| **Knowledge Vault** | Owner-only SQLite, content-addressed fragments, relation revisions, FTS, and append-only audit chain |
| **Knowledge Capsule** | Bounded context for one task with sources, selection reasons, gaps, budgets, and a Vault audit anchor |
| **Explain / Run / Feedback records** | Replay retrieval, Agent use, and feedback; feedback can create only a review-pending artifact |

### Design principles

- **Sources first:** source bytes and fragments remain independent. Graphs, embeddings, summaries,
  pages, and rankings are removable derived data.
- **Human governance:** compilers, models, imported packages, and Agent feedback may create only a
  proposal or quarantine. Explicit review is the sole activation path.
- **Stable identity:** Identity v2 separates logical source, Source Revision, Compilation,
  Knowledge Revision, and Governance Revision while retaining rename, move, split, merge, and
  historical relationships.
- **Bounded delivery:** DeepLaw compiles task duties before fusing exact, BM25, Source Tree,
  reviewed graph, temporal, feedback, and explicitly optional Dense/reranker candidates. A score
  cannot change authority.
- **Local ownership:** one OS user, local persistence, no default telemetry, and no remote listener.
  Markdown and Obsidian are rebuildable views; SQLite remains canonical.
- **Read-only Agent surface:** an Agent may retrieve a Capsule but cannot use MCP to remember,
  learn, approve, import, revoke, delete, or administer knowledge.

## Connect an Agent

DeepLaw exposes two isolated optional plugins:

| Plugin | Content | MCP tool | Write boundary |
| --- | --- | --- | --- |
| `deeplaw-knowledge-os` | General project knowledge, decisions, constraints, experience, and tool results | `knowledge_support` | Permanently read-only; local CLI owns administrative writes |
| `deeplaw` | Version-aware Chinese Legal Pack | `law_support` | Permanently read-only; separate process and store from the general Vault |

Codex, Claude Code, and OpenCode use thin host-specific adapters. Plugins are installed and enabled
explicitly and never take over unrelated coding or data work. See
[`docs/AGENT_ADAPTERS.md`](docs/AGENT_ADAPTERS.md) for configuration, install, upgrade, removal, and
dual-product isolation.

## v0.7.0 at a glance

| Surface | Current implementation |
| --- | --- |
| **Ingestion and structure** | Markdown/TXT, HTML, PDF, DOCX, PPTX, XLSX, EPUB, code, JSON/YAML/TOML, CSV/TSV, SQL, conversations, and tool results; Source IR / Tree retains locators, order, and hashes |
| **Compilation and governance** | Deterministic-v2 many-to-many compiler, proposal/quarantine, individual and batch review, lineage, temporal relations, and carry-forward proposals |
| **Recall and explanation** | Query Plan, exact/BM25/structure/graph/temporal/feedback channels, fusion, Knowledge Duties, token budgets, Explain Trace, and explicit gaps |
| **Local operator surfaces** | Golden CLI, resumable jobs, curses Workbench, Markdown/Obsidian/JSON Canvas projection, isolated Inbox, and Skill Factory |
| **Reliability** | Snapshot/restore, migration/rollback, GC, forgetting, doctor, corruption/lock/permission checks, POSIX owner-only permissions, and native Windows ACL/reparse gates |
| **Optional derived capabilities** | Removable Discovery Index bound to exact model/Vault/source/index bytes and a manifest-pinned local reranker; neither enters default MCP/Context retrieval |

For exact status, boundaries, and commands, use
[`docs/KNOWLEDGE_OS.md`](docs/KNOWLEDGE_OS.md) and
[`docs/CLI_LIFECYCLE.md`](docs/CLI_LIFECYCLE.md).

## Security boundaries

- Imported text is always untrusted data. Review cannot let it override host, repository,
  developer, or current-user instructions.
- Restricted knowledge, local Vault paths, inactive proposals, and unbounded graph traversal never
  cross MCP.
- Case-private documents, facts, chats, and identifiers stay outside the Knowledge OS, Legal Pack,
  caches, logs, and query corpora.
- General Knowledge Assets always carry `legal_authority: false`. Official legal sources exist only
  in a separate, signed, immutable Legal Pack release.
- Dense retrieval, rerankers, model compilers, and generated views cannot approve knowledge, decide
  legal validity, or invent missing sources.
- The repository includes auditable benchmark and evaluator tooling, but does not present incomplete
  external execution as a performance conclusion.

See [`SECURITY.md`](SECURITY.md) for the threat model and private reporting channel.

## Open-source collaboration

DeepLaw is open source under the [Apache License 2.0](LICENSE). Reproducible bugs, Source Adapters,
cross-platform regressions, documentation, and bounded retrieval improvements are welcome:

- read [`CONTRIBUTING.md`](CONTRIBUTING.md) and [`CODE_OF_CONDUCT.md`](CODE_OF_CONDUCT.md);
- open an [Issue](https://github.com/Eysn0130/DeepLaw/issues) with a minimal reproduction, expected
  boundary, and environment details;
- use [`ROADMAP.md`](ROADMAP.md) for the long-term direction;
- download versioned wheel, sdist, OCI, SBOM, and verification material from
  [Releases](https://github.com/Eysn0130/DeepLaw/releases).

Do not commit legal source documents, generated release databases, credentials, model weights,
private notes, or local paths containing user material.

## Documentation

| Topic | Entry point |
| --- | --- |
| Knowledge OS contract | [`docs/KNOWLEDGE_OS.md`](docs/KNOWLEDGE_OS.md) |
| Golden CLI, Workbench, and lifecycle | [`docs/CLI_LIFECYCLE.md`](docs/CLI_LIFECYCLE.md) |
| Local single-user architecture and product isolation | [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md) |
| Agent and MCP adapters | [`docs/AGENT_ADAPTERS.md`](docs/AGENT_ADAPTERS.md) |
| Install, upgrade, and rollback | [`docs/INSTALL_UPGRADE_ROLLBACK.md`](docs/INSTALL_UPGRADE_ROLLBACK.md) |
| Benchmark evidence and external protocol | [`docs/BENCHMARKS.md`](docs/BENCHMARKS.md) · [`docs/EXTERNAL_BENCHMARK_PROTOCOL.md`](docs/EXTERNAL_BENCHMARK_PROTOCOL.md) |
| v0.7 acceptance and release notes | [`docs/V0_7_ACCEPTANCE_MATRIX.md`](docs/V0_7_ACCEPTANCE_MATRIX.md) · [`docs/RELEASE_NOTES_v0.7.0.md`](docs/RELEASE_NOTES_v0.7.0.md) |
| Third-party components | [`THIRD_PARTY_NOTICES.md`](THIRD_PARTY_NOTICES.md) |

---

<p align="center">
  <strong>Local sources in. Verifiable knowledge out. Agent writes stay review-gated.</strong>
</p>
