# DeepLaw v0.13 bounded upstream research

Status: **design evidence only**, researched 2026-08-08, reconciled with the 2026-08-17
architecture freeze, augmented by the named 2026-08-18 product-closure review, and updated with a
2026-08-20 current observation. This report records concepts considered for v0.13; it is not evidence that any
target capability is shipped. Package/main remain `0.12.0 Beta`, active qualification is
`machine_evaluation_pending` under profile `machine_evaluated_no_human_attestation`, and Gate v8
remains pending. DeepLaw does not vendor or copy upstream implementation code in this work. The
exact commits below are frozen research anchors, not dependency pins or release inputs.

The frozen product boundary is three roles on one governed kernel: Task Continuity / Governed
Project Knowledge, Source-native Evidence Library, and Living Wiki. They share one Context Compiler;
research does not authorize a fourth product, database, Knowledge kind, Relation predicate, page
family, Host adapter, Agent runtime, connector, telemetry path or cloud control plane. Automatic
transcript memory remains prohibited. The current Provider advertisement is input v7/output v6 with
only `query`, `context`, and `explain`; older Provider versions remain internal compatibility.

## 2026-08-18 named product-closure anchors

The current named comparison is recorded in
[`UPSTREAM_PRODUCT_CLOSURE_2026-08-18.md`](UPSTREAM_PRODUCT_CLOSURE_2026-08-18.md). It deliberately
separates a frozen qualification coordinate from a moving upstream branch observation:

| Upstream | Frozen v0.13 qualification coordinate | Exact 2026-08-18 research anchor | Observed moving branch at 2026-08-18 15:40 +08:00 | License posture |
| --- | --- | --- | --- | --- |
| OpenWiki | released v0.3.1, peeled commit `630eb9ec3fa22a4bed2d347fc3ea3a6a3bd22abc` | `21746ce996f3a69898883da58b122770f7dbd668` | `main` at the same research-anchor commit | MIT; behavior/reference review only |
| Tolaria | `v2026-08-11`, commit `cb45f26649a7500e0bdb5dd0b8f0412e9c1daf4d` | `40cc9f9479fef7bfe8a51a6df7e02fe11971f95e` | `main` at the same research-anchor commit | AGPL-3.0-or-later; no code copied |
| Obsidian API | `obsidian@1.13.2`, commit `cc1744324150c632416857c98964f87b1574a5fc` | the same exact commit | `master` at the same commit | MIT type definitions; no Desktop implementation claim |
| Ekgardt/llm-wiki | no named v0.13 qualification comparator; the protocol retains only an LLM-Wiki behavior category | `350eec8a284e159b2e4cfd068d808cbf203a6cc5` | `main` at the same research-anchor commit | MIT; behavior/reference review only |

The moving branches happened to equal the exact research anchors when observed. That coincidence
does not convert a branch name into an immutable input and does not rotate the qualification
coordinates. Ekgardt/llm-wiki remains a named research comparator for this development review; it
does not silently replace the protocol's broader LLM-Wiki behavior category.

## 2026-08-20 current observation (not a qualification rotation)

The following is a current observation dated **2026-08-20**. A **qualification pin** is the frozen
protocol input; a **released comparator** is a named release coordinate observed for comparison; a
**moving HEAD** is a branch or current repository coordinate and is not an immutable qualification
input. The observation does not rotate the frozen pins or alter the immutable
[`UPSTREAM_PRODUCT_CLOSURE_2026-08-18.md`](UPSTREAM_PRODUCT_CLOSURE_2026-08-18.md) report.

| Upstream | Qualification pin (retained) | Released comparator observed 2026-08-20 | Moving HEAD observed 2026-08-20 | Execution status |
| --- | --- | --- | --- | --- |
| OpenWiki | released v0.3.1, peeled `630eb9ec3fa22a4bed2d347fc3ea3a6a3bd22abc` | release `v0.3.3/355f4f68e71bd024631cdcff7aa871c3e72435da` | `main` `46c0a3d53011a1f4916052187288dc5b4651c292` | `not_executed` |
| Tolaria | `v2026-08-11` / `cb45f26649a7500e0bdb5dd0b8f0412e9c1daf4d` | release `v2026-08-19/cf9b0c8b9fca7cd9556da4b0401e207626a70384` | `main` `367a91416477c90bbfae766dc06add3de6ae75a7` | `not_executed` |
| Obsidian API | `obsidian@1.13.2` / `cc1744324150c632416857c98964f87b1574a5fc` | not separately rotated; exact API coordinate retained | `master` `cc1744324150c632416857c98964f87b1574a5fc` | `not_executed` |
| Ekgardt/llm-wiki | no named v0.13 qualification comparator; the protocol retains the LLM-Wiki behavior category | none | `main` `350eec8a284e159b2e4cfd068d808cbf203a6cc5` | `not_executed` |

These are source-reading and coordinate observations only. Source reading does not establish parity,
integration, product reachability or qualification; all upstream execution status remains
`not_executed`. Execution status: `not_executed` for every row.

## Frozen references

| Upstream | Exact reference | License posture | Relevance |
|---|---|---|---|
| Codex official documentation | [Customization](https://learn.chatgpt.com/docs/customization/overview), [AGENTS.md](https://learn.chatgpt.com/docs/agent-configuration/agents-md), [Subagents](https://learn.chatgpt.com/docs/agent-configuration/subagents), [MCP](https://learn.chatgpt.com/docs/extend/mcp), [Hooks](https://learn.chatgpt.com/docs/hooks), read 2026-08-07 | documentation reference only | Durable project guidance, short Skills, MCP server instructions, bounded Workers and opt-in lifecycle events |
| Claude Code official documentation | [Memory](https://code.claude.com/docs/en/memory), [MCP](https://code.claude.com/docs/en/mcp), [Subagents](https://code.claude.com/docs/en/sub-agents), [Hooks](https://code.claude.com/docs/en/hooks), read 2026-08-07 | documentation reference only | Host-neutral envelope mapping and optional prompt/compact/stop lifecycle |
| DeepWiki (Cognition) | [product](https://deepwiki.com/) and [launch note](https://cognition.com/blog/deepwiki), observed 2026-08-07 | hosted, closed-source product; public behavior only | Human-oriented repository table of contents, generated explanations and interactive navigation; no source-level claims |
| DeepWiki-Open | [`4181daa5ebde79a1baf8e92a09dd874f8b74411b`](https://github.com/AsyncFuncAI/deepwiki-open/tree/4181daa5ebde79a1baf8e92a09dd874f8b74411b) on `main` | MIT; concepts only in this change | Repository analysis, structured Wiki generation, diagrams and code-centric guided tours |
| OpenDeepWiki | [`a71a441a017bb3b8d1a0064afbdf22a3ad9d5383`](https://github.com/AIDotNet/OpenDeepWiki/tree/a71a441a017bb3b8d1a0064afbdf22a3ad9d5383) on `main` | MIT; concepts only in this change | Guided page families, hierarchy and navigation planning |
| Guanlan | [`1394a41454559f2f5373719c808fed9fe872dd88`](https://github.com/jin-bo/guanlan/tree/1394a41454559f2f5373719c808fed9fe872dd88) on `main` | Apache-2.0; concepts only in this change | Local incremental Markdown Wiki, immutable raw inputs, link checks, maintenance commands and read-only MCP |
| Obsidian Help | `067a3b99f6d24da95bf8dafcbe1c39e3ee71b10a` on `master` | no repository-wide SPDX license asserted; reference only | Open Markdown, properties, Wikilinks, backlinks, graph and Canvas user surfaces |
| OpenWiki released v0.3.1 | [`630eb9ec3fa22a4bed2d347fc3ea3a6a3bd22abc`](https://github.com/langchain-ai/openwiki/tree/630eb9ec3fa22a4bed2d347fc3ea3a6a3bd22abc) (peeled commit) | MIT; no code reuse | Frozen Kernel compatibility baseline identity |
| OpenWiki review snapshot | [`7531d615216e8cbccf464f66cfbbae3668871c84`](https://github.com/langchain-ai/openwiki/tree/7531d615216e8cbccf464f66cfbbae3668871c84) (package-version-0.3.1 review snapshot) | MIT (`LICENSE` at the exact commit); no code reuse | Layered CLI/agent/provider/connector architecture and bounded read-only MCP connector policy |
| Google OKF / Knowledge Catalog | [`374e0bc4c644310ff56cdf9c0fe81eccdec862b0`](https://github.com/GoogleCloudPlatform/knowledge-catalog/tree/374e0bc4c644310ff56cdf9c0fe81eccdec862b0) on `main` | Apache-2.0 (`LICENSE.md` and `okf/LICENSE.md`); no code reuse | OKF v0.2 Markdown/YAML interchange and catalog `EntryLink` projection |
| Obsidian API | [`cc1744324150c632416857c98964f87b1574a5fc`](https://github.com/obsidianmd/obsidian-api/tree/cc1744324150c632416857c98964f87b1574a5fc) on `master`; exact package `obsidian@1.13.2` | MIT (`LICENSE.md`/`package.json`); no application implementation reuse | Public plugin type surface (`App`, `Vault`, `Workspace`, `MetadataCache`) and Canvas data types |
| Tolaria | [`4cced2027998c4affdf65385f9683b7e8a03c041`](https://github.com/refactoringhq/tolaria/tree/4cced2027998c4affdf65385f9683b7e8a03c041) on `main` | AGPL-3.0 (`LICENSE`); no code reuse | Files-first Markdown vault, dynamic Wikilink relationships, and external MCP/editor boundary |
| MCP specification | [`9d4a9115126f1356f4b189af3266c1839a4e9bbb`](https://github.com/modelcontextprotocol/modelcontextprotocol/tree/9d4a9115126f1356f4b189af3266c1839a4e9bbb) on `main` | Mixed transition in root `LICENSE`: new code/spec contributions Apache-2.0, non-spec docs CC-BY-4.0, unrelicensed legacy contributions MIT; do not reduce to one SPDX | `resources/list/read`, `tools/list/call`, capability declarations, and opaque-cursor pagination |
| Graphiti | [`425bf2481b51437e43455e09d241c5f46e3d95f3`](https://github.com/getzep/graphiti/tree/425bf2481b51437e43455e09d241c5f46e3d95f3) on `main` | Apache-2.0; concepts only in this change | Episode provenance, valid time and incremental temporal-graph lessons |
| GraphRAG | [`14a00ad88fc33cf2b52f4f113f25807556f8e25e`](https://github.com/microsoft/graphrag/tree/14a00ad88fc33cf2b52f4f113f25807556f8e25e) on `main` | MIT (`LICENSE`); concepts only in this change | Configurable graph-based indexing/query pipeline and explicit context/source reporting |
| Mem0 | [`4debc58a83377b18be81ae1e5969a300736b2fac`](https://github.com/mem0ai/mem0/tree/4debc58a83377b18be81ae1e5969a300736b2fac) on `main` | Apache-2.0; concepts only in this change | Small memory API and memory-quality/evaluation lessons |
| Cognee | [`38eece5bbb0cb9f5706fed908abd16dba0f5505e`](https://github.com/topoteretes/cognee/tree/38eece5bbb0cb9f5706fed908abd16dba0f5505e) on `main` | Apache-2.0; concepts only in this change | Pipeline/job concepts and graph/vector memory evaluation surface |
| Letta | [`ff19ffeafeb54bd2a7dc5d4a552f10191732a235`](https://github.com/letta-ai/letta/tree/ff19ffeafeb54bd2a7dc5d4a552f10191732a235) on `main` | Apache-2.0; concepts only in this change | Persistent Agent state, memory-block and context-efficiency lessons |
| LightRAG | [`b33c6b0812cddf39206e48a9810112e51f025274`](https://github.com/HKUDS/LightRAG/tree/b33c6b0812cddf39206e48a9810112e51f025274) on `main` | MIT; concepts only in this change | Local/global/hybrid retrieval modes, incremental graph maintenance and ablation shape |

The existing `docs/UPSTREAM_CAPABILITY_MATRIX.md` remains the broader retrieval, graph and memory
comparison. This report is a v0.13 delta rather than a replacement.

The qualification baseline binds OpenWiki released v0.3.1 to peeled commit
`630eb9ec3fa22a4bed2d347fc3ea3a6a3bd22abc`. The separately reviewed
`7531d615216e8cbccf464f66cfbbae3668871c84` coordinate is retained only as a
package-version-0.3.1 review snapshot for the notes below.

## Exact-commit evidence and audit notes

Each commit link below resolves to a public GitHub commit object with the stated full SHA. License
and interface links use the same SHA; they are research evidence only and are not copied into the
DeepLaw package.

- **OpenWiki package-version-0.3.1 review snapshot — `langchain-ai/openwiki@7531d615216e8cbccf464f66cfbbae3668871c84`.** The exact
  [`commit`](https://github.com/langchain-ai/openwiki/commit/7531d615216e8cbccf464f66cfbbae3668871c84)
  contains an MIT `LICENSE`. Its
  [`architecture/overview.md`](https://raw.githubusercontent.com/langchain-ai/openwiki/7531d615216e8cbccf464f66cfbbae3668871c84/openwiki/architecture/overview.md)
  documents the CLI/agent/provider/connector layers, while
  [`integrations/connectors.md`](https://raw.githubusercontent.com/langchain-ai/openwiki/7531d615216e8cbccf464f66cfbbae3668871c84/openwiki/integrations/connectors.md)
  documents connector state and a read-only MCP connector policy. OpenWiki's own wiki/cache and
  credential lifecycle are not DeepLaw canonical state.
- **Google OKF — `GoogleCloudPlatform/knowledge-catalog@374e0bc4c644310ff56cdf9c0fe81eccdec862b0`.**
  The exact [`commit`](https://github.com/GoogleCloudPlatform/knowledge-catalog/commit/374e0bc4c644310ff56cdf9c0fe81eccdec862b0)
  carries Apache-2.0 in both root [`LICENSE.md`](https://raw.githubusercontent.com/GoogleCloudPlatform/knowledge-catalog/374e0bc4c644310ff56cdf9c0fe81eccdec862b0/LICENSE.md)
  and [`okf/LICENSE.md`](https://raw.githubusercontent.com/GoogleCloudPlatform/knowledge-catalog/374e0bc4c644310ff56cdf9c0fe81eccdec862b0/okf/LICENSE.md).
  [`okf/SPEC.md`](https://raw.githubusercontent.com/GoogleCloudPlatform/knowledge-catalog/374e0bc4c644310ff56cdf9c0fe81eccdec862b0/okf/SPEC.md)
  defines OKF v0.2 as plain Markdown/YAML with provenance and trust signals but no central
  authority; the commit's `dataplex.ts` adds catalog `EntryLink` projection APIs. These are
  interchange/catalog surfaces, not an Authority source or a replacement for the Ledger.
- **Obsidian API — `obsidianmd/obsidian-api@cc1744324150c632416857c98964f87b1574a5fc`.** The exact
  [`commit`](https://github.com/obsidianmd/obsidian-api/commit/cc1744324150c632416857c98964f87b1574a5fc)
  contains MIT [`LICENSE.md`](https://raw.githubusercontent.com/obsidianmd/obsidian-api/cc1744324150c632416857c98964f87b1574a5fc/LICENSE.md)
  and `package.json` declares `obsidian@1.13.2`. The public
  [`README.md`](https://raw.githubusercontent.com/obsidianmd/obsidian-api/cc1744324150c632416857c98964f87b1574a5fc/README.md)
  and [`obsidian.d.ts`](https://raw.githubusercontent.com/obsidianmd/obsidian-api/cc1744324150c632416857c98964f87b1574a5fc/obsidian.d.ts)
  expose plugin types, not the desktop implementation; [`canvas.d.ts`](https://raw.githubusercontent.com/obsidianmd/obsidian-api/cc1744324150c632416857c98964f87b1574a5fc/canvas.d.ts)
  is a derived Canvas data surface. The repository's prior `obsidian@1.13.1` wording is an audit
  dependency drift; this research does not upgrade v0.13 and real desktop E2E remains unexecuted.
- **Tolaria — `refactoringhq/tolaria@4cced2027998c4affdf65385f9683b7e8a03c041`.** The exact
  [`commit`](https://github.com/refactoringhq/tolaria/commit/4cced2027998c4affdf65385f9683b7e8a03c041)
  carries AGPL-3.0 in [`LICENSE`](https://raw.githubusercontent.com/refactoringhq/tolaria/4cced2027998c4affdf65385f9683b7e8a03c041/LICENSE).
  Its [`README.md`](https://raw.githubusercontent.com/refactoringhq/tolaria/4cced2027998c4affdf65385f9683b7e8a03c041/README.md)
  and [`docs/ARCHITECTURE.md`](https://raw.githubusercontent.com/refactoringhq/tolaria/4cced2027998c4affdf65385f9683b7e8a03c041/docs/ARCHITECTURE.md)
  describe a files-first Markdown vault; ADR 0011 documents stdio/WebSocket MCP note tools,
  including mutation. AGPL and application-local filesystem authority require an editor/host
  adapter boundary, not copied code or canonical writes.
- **MCP specification — `modelcontextprotocol/modelcontextprotocol@9d4a9115126f1356f4b189af3266c1839a4e9bbb`.**
  The exact [`commit`](https://github.com/modelcontextprotocol/modelcontextprotocol/commit/9d4a9115126f1356f4b189af3266c1839a4e9bbb)
  root [`LICENSE`](https://raw.githubusercontent.com/modelcontextprotocol/modelcontextprotocol/9d4a9115126f1356f4b189af3266c1839a4e9bbb/LICENSE)
  explicitly records an MIT-to-Apache-2.0 transition: new code/spec contributions are Apache-2.0,
  non-spec documentation is CC-BY-4.0, and unrelicensed legacy contributions remain MIT. The
  [`resources`](https://raw.githubusercontent.com/modelcontextprotocol/modelcontextprotocol/9d4a9115126f1356f4b189af3266c1839a4e9bbb/docs/specification/2026-07-28/server/resources.mdx),
  [`tools`](https://raw.githubusercontent.com/modelcontextprotocol/modelcontextprotocol/9d4a9115126f1356f4b189af3266c1839a4e9bbb/docs/specification/2026-07-28/server/tools.mdx)
  and [`pagination`](https://raw.githubusercontent.com/modelcontextprotocol/modelcontextprotocol/9d4a9115126f1356f4b189af3266c1839a4e9bbb/docs/specification/2026-07-28/server/utilities/pagination.mdx)
  specifications define separate resource/tool capabilities and opaque cursors. License status
  must remain file/contribution-specific rather than being compressed to one SPDX label.
- **Graphiti — `getzep/graphiti@425bf2481b51437e43455e09d241c5f46e3d95f3`.** The exact
  [`commit`](https://github.com/getzep/graphiti/commit/425bf2481b51437e43455e09d241c5f46e3d95f3)
  carries Apache-2.0 [`LICENSE`](https://raw.githubusercontent.com/getzep/graphiti/425bf2481b51437e43455e09d241c5f46e3d95f3/LICENSE).
  Its [`README.md`](https://raw.githubusercontent.com/getzep/graphiti/425bf2481b51437e43455e09d241c5f46e3d95f3/README.md)
  and [`graphiti.py`](https://raw.githubusercontent.com/getzep/graphiti/425bf2481b51437e43455e09d241c5f46e3d95f3/graphiti_core/graphiti.py)
  describe temporal entities/facts/episodes and pluggable graph/LLM/embedding clients; the
  experimental [`MCP server`](https://raw.githubusercontent.com/getzep/graphiti/425bf2481b51437e43455e09d241c5f46e3d95f3/mcp_server/README.md)
  includes graph mutations. It can inform bounded discovery experiments only and cannot supply
  DeepLaw Authority or canonical graph state.
- **GraphRAG — `microsoft/graphrag@14a00ad88fc33cf2b52f4f113f25807556f8e25e`.** The exact
  [`commit`](https://github.com/microsoft/graphrag/commit/14a00ad88fc33cf2b52f4f113f25807556f8e25e)
  carries Microsoft MIT [`LICENSE`](https://raw.githubusercontent.com/microsoft/graphrag/14a00ad88fc33cf2b52f4f113f25807556f8e25e/LICENSE).
  [`README.md`](https://raw.githubusercontent.com/microsoft/graphrag/14a00ad88fc33cf2b52f4f113f25807556f8e25e/README.md)
  identifies a graph-based indexing/transformation pipeline and warns that it is a demonstration,
  not an officially supported Microsoft offering; the API notebook shows `build_index` and
  `global_search` returning context/source material. Its summaries, communities and rankings are
  discovery aids, not Authority or evidence.

## v0.14/v0.15 interoperability route (not v0.13 implementation)

The frozen references support documentation-only research and future adapter qualification. They
do not expand the v0.13 source-candidate contract:

- OKF is an **interchange projection only**. Frontmatter provenance, credibility, trust signals,
  links and catalog entry links cannot create DeepLaw identity, Authority, verification or scope.
- MCP **Resources remain read-only** for Wiki, Source and Schema views. Any mutation remains behind
  the owner-granted `knowledge_sink`; MCP tool discovery, pagination and transport do not widen a
  grant or establish Authority.
- Obsidian API, Bases and Canvas are **derived editor views**. Tolaria is an **editor/host surface**
  only; OpenWiki is an isolated docs/connector reference. Neither owns the DeepLaw Ledger or
  canonical identity, and the AGPL Tolaria implementation is not copied.
- Graphiti and GraphRAG graph traversal, communities, centrality, summaries and rerankers are
  **discovery/navigation aids only**. They cannot establish trust, legal Authority, current
  pointers or mutation permission.
- Stable identity, provenance, Authority, lifecycle, temporal state and capability grants remain
  solely in DeepLaw's governed domain services. Each future adapter needs a pinned-commit/license
  manifest, closed environment, secret-canary proof and independent E2E evidence before any status
  can change; none of these routes is `shipped` in v0.13.

## Adopted design principles

### Codex and Claude Code

- Keep repository-wide invariants small in `AGENTS.md`; knowledge content stays in the governed
  Vault and reaches Agents through MCP.
- Put the recommended read sequence and non-negotiable Authority boundary in the first 512 MCP
  instruction characters. Codex documents this as the self-contained decision window for server
  instructions.
- Use short, task-specific Skills for query, compile, verify, refresh, navigate and promote. A
  Skill describes a workflow; it cannot grant DeepLaw mutation capability.
- Treat host hooks as optional ephemeral context transport. Prompt, compact, recovery and stop
  events may construct or refresh a bounded Context Envelope, but they do not write knowledge,
  mint a Grant, call a model, or transmit secrets.
- Keep Workers bounded and independently verifiable. Subagent output is context isolation, not a
  new Authority or release verdict.

### Obsidian

- Continue to project ordinary Markdown, YAML frontmatter, Wikilinks and JSON Canvas so humans can
  navigate with core editor features.
- Add a stable Page Registry, Link Index and Resolver behind the files. Obsidian paths remain a
  presentation address; they do not become stable identity or Authority.
- Preserve user-authored, unowned files during rebuild and profile switches. Derived files must be
  recognizable only through a verified ownership manifest.
- Default scale behavior uses sharded indexes and no per-object Canvas. Local graph and Canvas are
  bounded, explicit, on-demand views.

### Tolaria

Tolaria's frozen release confirms several useful product seams without changing DeepLaw's trust
model:

- vault-neutral MCP registration resolves active mounted workspaces at call time;
- an external clean-file change causes the active note to refresh while unsaved editor content is
  preserved;
- `tolaria://<vault-slug>/<relative-path>` is navigation-only and intentionally path-based;
- the Agent prompt bridge is an internal UI event bus;
- Tolaria's writable MCP note tools are application-local conveniences, not a DeepLaw Grant.

DeepLaw therefore uses the shared Vault, read-only `knowledge_support`, stable Wiki Resolver and an
ephemeral Context Envelope as the portable integration. It does **not** call Tolaria's
`update_note`/`append_to_note` to promote canonical knowledge, copy AGPL implementation code, or
infer stable DeepLaw identity from a Tolaria path. The frozen Tolaria MCP contract does not expose
a general third-party extension that can obtain the active-note identity and inject a custom
preview/promotion UI. Until a real harness proves such a seam, the full active-note product loop
must be reported `integration_limited`, not simulated as passed.

### DeepWiki, DeepWiki-Open, OpenDeepWiki and Guanlan

Adopt the idea of an explicit, governed coverage plan with page families, required topics,
hierarchy, guided tours, code-map sections, page limits and sharding. In DeepLaw the plan is Owner
configuration or a governed draft; it is not knowledge and contains no generated semantic prose.
The deterministic projector renders only committed Knowledge Revisions and reports missing
coverage as Gaps. Rebuild remains offline and model-free.

The four projects are intentionally not conflated. Cognition DeepWiki is a hosted product, so this
review records only its public product behavior. DeepWiki-Open and OpenDeepWiki are distinct MIT
repositories with model-backed repository-documentation pipelines. Guanlan is an Apache-2.0 local
Markdown Wiki project whose README describes immutable `raw/`, Agent-maintained Wiki pages,
deterministic checks, maintenance commands and read-only MCP. These are useful product patterns,
but none supplies DeepLaw's stable revision identity, trusted Ledger, separated Authority,
fail-closed admission or statement-to-evidence contract. DeepLaw therefore reimplements the
coverage, navigation and maintenance seams against its own governed objects and does not import a
model-generated Wiki as evidence or canonical state.

### Graphiti, Mem0, Cognee, Letta and LightRAG

The broader comparison and prior fixed references remain in
`docs/UPSTREAM_CAPABILITY_MATRIX.md`; the table above refreshes the repositories to exact commits
observed for this v0.13 review. The bounded takeaways are temporal validity and episode provenance,
memory lifecycle/evaluation, pipeline observability, context-efficiency and retrieval ablation.
They do not justify importing an Agent runtime, automatic memory writes, an external graph/vector
database, provider calls, or upstream self-reported benchmark scores. DeepLaw's candidate keeps
those concerns behind its existing governance, deterministic commit and bounded provider-visible
context contracts. Named comparative quality remains `not_executed`, so
`competitive_claim_eligible=false`.

## Explicitly rejected designs

- A remote or editor-owned canonical database.
- Model-generated summaries during rebuild.
- Filesystem paths, titles, aliases, frontmatter or Wikilinks as Authority or capability.
- Repeated raw-source processing when admitted compiled knowledge covers the task.
- A hidden fallback from compiled knowledge to raw fragments.
- Direct editor writes as automatic promotion into canonical knowledge.
- Long knowledge payloads in `AGENTS.md`, hook output or Skill instructions.
- Copying Tolaria AGPL code or Obsidian proprietary implementation behavior into DeepLaw.
- Claiming a real Tolaria/Obsidian product integration from a deterministic substitute.

## PRD 1.1 evidence review

The 2026-08-08 PRD review added product-level constraints without expanding the v0.13 candidate:

- [C2PA's official explainer](https://c2pa.org/specifications/specifications/2.2/explainer/Explainer.html)
  states that valid provenance does not by itself establish factual truth. DeepLaw therefore keeps
  integrity, provenance, Authority, verification, corroboration and applicability separate.
- [WiCER](https://arxiv.org/abs/2605.07068) reports a compilation gap in blindly generated LLM
  Wikis and motivates coverage probes, explicit evidence fallback and targeted refinement. It is a
  research result, not proof that one compiler strategy generalizes to DeepLaw or legal material.
- [Mem2ActBench](https://arxiv.org/abs/2601.19935) evaluates whether recalled memory changes tool
  use and parameter grounding, while
  [MemSecBench](https://arxiv.org/abs/2607.27080) follows a write-execute-forget security
  lifecycle. These motivate end-task and lifecycle gates beyond Recall@K.
- [Governed Evolving Memory](https://arxiv.org/abs/2605.26252) frames memory correctness as a state
  trajectory across ingestion, revision, forgetting and retrieval. DeepLaw adopts that evaluation
  question without adopting its property-graph prototype.
- The [MCP 2026-07-28 release](https://blog.modelcontextprotocol.io/posts/2026-07-28/) is
  stateless-first. DeepLaw therefore does not bind durable continuity or knowledge identity to an
  MCP connection and treats capability discovery as an adapter surface only.
- [OKF v0.2](https://github.com/GoogleCloudPlatform/knowledge-catalog/blob/374e0bc4c644310ff56cdf9c0fe81eccdec862b0/okf/SPEC.md)
  distinguishes provenance from attested computation and leaves runtime packaging open. DeepLaw
  may project this pattern later but remains outside the execution and capability-grant role.

These sources support PRD invariants and evaluation design only. The papers are recent research,
not release evidence, and no competitive or implementation claim follows from citing them.

## PRD 1.2 adversarial scope review

The second 2026-08-08 review challenged both deletions and additions instead of assuming that a
smaller PRD was automatically better.

### Updated external evidence

- Current [Codex Memories](https://learn.chatgpt.com/docs/customization/memories) and
  [Claude Code memory](https://code.claude.com/docs/en/memory) both provide generated local
  cross-session recall. This invalidates any product thesis that Host memory is simply absent.
  DeepLaw's defensible job is portable, project- and task-lineage-specific state plus governed
  evidence, not duplicating preference or transcript recall.
- [OpenWiki package-version-0.3.1 review snapshot at `7531d615216e8cbccf464f66cfbbae3668871c84`](https://github.com/langchain-ai/openwiki/tree/7531d615216e8cbccf464f66cfbbae3668871c84)
  now demonstrates self-maintaining code/personal Wikis, source connectors, CI updates, a graph
  viewer and OKF output. Those features validate open Wiki demand but do not prove generated pages
  are complete, authoritative or safe to promote. DeepLaw does not copy its broad connector,
  credential, telemetry or root-instruction mutation choices by default.
- [obsidian-wiki at `5ef66b6bec8b26bab6594ac37fb4d8371469fbab`](https://github.com/Ar9av/obsidian-wiki/tree/5ef66b6bec8b26bab6594ac37fb4d8371469fbab)
  demonstrates manifest-based incremental compilation, progressive page loading, session search,
  lint and cross-agent Markdown Skills. Its full session-history ingestion is deliberately not a
  DeepLaw default; the useful user outcome is recovered through a content-minimized Run Timeline.
- [Graphiti at `425bf2481b51437e43455e09d241c5f46e3d95f3`](https://github.com/getzep/graphiti/tree/425bf2481b51437e43455e09d241c5f46e3d95f3),
  [Letta at `ff19ffeafeb54bd2a7dc5d4a552f10191732a235`](https://github.com/letta-ai/letta/tree/ff19ffeafeb54bd2a7dc5d4a552f10191732a235),
  and [Mem0 at `4debc58a83377b18be81ae1e5969a300736b2fac`](https://github.com/mem0ai/mem0/tree/4debc58a83377b18be81ae1e5969a300736b2fac)
  validate temporal graphs, stateful agents and multi-level memory. They do not justify making
  DeepLaw an Agent runtime, personal-profile service, external graph database, or automatic memory
  writer.
- [MemOps](https://arxiv.org/abs/2607.12893) requires operation traces for remember, update,
  forget and reflect; [MemoryArena](https://arxiv.org/abs/2602.16313) shows that high recall scores
  do not imply success on interdependent multi-session tasks. These findings require task-lineage,
  wrong-target, concurrency and action-level evaluation.

These repositories are reference-only under their recorded licenses; no code or dependency was
imported.

### Deletion audit

| Earlier deletion or simplification | Verdict | PRD 1.2 disposition |
| --- | --- | --- |
| Fixed seven-field Checkpoint taxonomy | Correct deletion | Required semantic contents remain; exact enums and field names stay in versioned contracts |
| Package version, candidate status and benchmark constants | Correct deletion | Mutable facts remain in disposition and evaluation documents |
| One version-specific Gate A/B/C/D ladder | Correct deletion | Capabilities qualify independently as Target, Implemented, Qualified or Released |
| Full transcript and Host-memory ingestion | Correct safety boundary, but incomplete user outcome | Keep raw transcripts out; add an owner-searchable, content-minimized Run Timeline with optional opaque Host reference |
| Default Canvas, community and relation-path page generation | Correct for materialized views | Restore bounded typed-relation traversal as core; keep per-object presentation optional |
| Single-Revision revert | Earlier wording was too broad | Require semantic restore by a new revision/recovery event; continue to forbid audit rewind and dependency-blind pointer rollback |
| Root `AGENTS.md`/README PRD links during this review | Correct temporary deletion | Those files are frozen Gold inputs; Task Cards cite the PRD until a deliberate protocol-version rotation |
| Attested procedural knowledge as a differentiator | Speculative and non-core | Remove from stable PRD; retain attestation only as research or an interchange projection subject to feature admission |

### Addition audit

The review retains provenance-versus-truth separation, Wiki compilation gaps, lifecycle memory,
selective forgetting, maintenance-debt metrics, stateless MCP requests and capability separation.
It also adds requirements that were previously missing: concurrent task/worktree lineage,
cross-Vault isolation, source-acquisition manifests, content-minimized Run discovery, Wiki ownership
classes, Wikilink-versus-typed-Relation separation, bounded relation paths, order-independent tail
retrieval, stale-head detection, disambiguation and semantic restore.

These are requirements, not claims that the current v0.13 candidate implements them. Each new
contract remains behind the PRD feature-admission gate and requires a failing external task before
runtime migration.

### Current candidate impact

- `knowledge_run_records_v4` currently binds writer, Host, task hash, scope, sensitivity, outcome
  hashes and time, but it has no canonical parent task-line, repository/worktree binding or
  conflict-aware concurrent-Checkpoint contract. `PRD-CONT-010` through `PRD-CONT-012` are therefore
  `Target`, not current behavior.
- The Page Registry, Link Index, Resolver, projection manifest and typed Relation store provide
  reusable primitives for `PRD-WIKI-010` through `PRD-WIKI-013`; editor ownership classes,
  end-to-end reconciliation and bounded path-task acceptance still require explicit mapping and
  external evidence.
- Query plans already bind audit heads and the candidate includes tail-recall remediation tests,
  but order invariance, stale-head handling and cross-Vault disambiguation must be mapped at every
  public seam before `PRD-CTX-013` through `PRD-CTX-015` can be called implemented.
- Revision history, lifecycle and recovery primitives exist, but semantic restore by a new revision
  with dependent-state validation is not claimed until its public contract, migration/recovery and
  external rollback task are demonstrated.

No runtime code, package version, release status, benchmark Gold, or competitive claim changes as
part of this PRD review.

## Consequences for v0.13 acceptance

The research supports the Page Registry/Link Index/Resolver, Coverage Specification, projection
profiles, split Skills, first-512 MCP instructions and host-neutral Context Envelope. It does not
satisfy real-host, machine-reference isolation, desktop product, cross-platform or
competitive-comparator gates;
those require separate executable evidence and remain `not_executed` until actually run.
