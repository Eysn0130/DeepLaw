# DeepLaw v0.13 bounded upstream research

Status: **design evidence**, researched 2026-08-07. This report records concepts considered for
v0.13; it is not evidence that any target capability is shipped. DeepLaw does not vendor or copy
upstream implementation code in this work.

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
| Obsidian API | [`cc1744324150c632416857c98964f87b1574a5fc`](https://github.com/obsidianmd/obsidian-api/tree/cc1744324150c632416857c98964f87b1574a5fc) on `master`; local package `obsidian@1.13.1` | MIT declarations/API surface; no application implementation reuse | Supported plugin types and APIs used by the local adapter |
| Tolaria | tag `v2026-07-22`, commit `e2cd718a518cc96d1081b6ec3aabefe3b6c77199` | AGPL-3.0; no code reuse | Mounted-workspace context, filesystem convergence, deep links, MCP registration and Agent UI boundary |
| Graphiti | [`425bf2481b51437e43455e09d241c5f46e3d95f3`](https://github.com/getzep/graphiti/tree/425bf2481b51437e43455e09d241c5f46e3d95f3) on `main` | Apache-2.0; concepts only in this change | Episode provenance, valid time and incremental temporal-graph lessons |
| Mem0 | [`4debc58a83377b18be81ae1e5969a300736b2fac`](https://github.com/mem0ai/mem0/tree/4debc58a83377b18be81ae1e5969a300736b2fac) on `main` | Apache-2.0; concepts only in this change | Small memory API and memory-quality/evaluation lessons |
| Cognee | [`38eece5bbb0cb9f5706fed908abd16dba0f5505e`](https://github.com/topoteretes/cognee/tree/38eece5bbb0cb9f5706fed908abd16dba0f5505e) on `main` | Apache-2.0; concepts only in this change | Pipeline/job concepts and graph/vector memory evaluation surface |
| Letta | [`ff19ffeafeb54bd2a7dc5d4a552f10191732a235`](https://github.com/letta-ai/letta/tree/ff19ffeafeb54bd2a7dc5d4a552f10191732a235) on `main` | Apache-2.0; concepts only in this change | Persistent Agent state, memory-block and context-efficiency lessons |
| LightRAG | [`b33c6b0812cddf39206e48a9810112e51f025274`](https://github.com/HKUDS/LightRAG/tree/b33c6b0812cddf39206e48a9810112e51f025274) on `main` | MIT; concepts only in this change | Local/global/hybrid retrieval modes, incremental graph maintenance and ablation shape |

The existing `docs/UPSTREAM_CAPABILITY_MATRIX.md` remains the broader retrieval, graph and memory
comparison. This report is a v0.13 delta rather than a replacement.

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

## Consequences for v0.13 acceptance

The research supports the Page Registry/Link Index/Resolver, Coverage Specification, projection
profiles, split Skills, first-512 MCP instructions and host-neutral Context Envelope. It does not
satisfy real-host, Human Gold, desktop product, cross-platform or competitive-comparator gates;
those require separate executable evidence and remain `not_executed` until actually run.
