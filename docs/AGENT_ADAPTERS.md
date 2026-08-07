# DeepLaw Agent Adapters

DeepLaw provides local adapters for Codex, Claude Code, OpenCode, Obsidian and Tolaria. The two default products are
read-only; an autonomous mutation capability is a third, separately enabled process and must not be
collapsed into either query surface:

| Product | Plugin | Process | Single leaf |
| --- | --- | --- | --- |
| Chinese Legal Pack | `deeplaw` | `deeplaw mcp --stdio` | `law_support` |
| Knowledge Asset core | `deeplaw-knowledge-os` | `deeplaw knowledge mcp --stdio` | `knowledge_support` |
| Autonomous mutation (not registered by default) | owner host config | `deeplaw knowledge sink mcp --grant-id … --stdio` | `knowledge_sink` |

This document describes adapter behavior only. Corpus building, release
governance, and retrieval internals are separate concerns.

## Acceptance status

| Surface | Status | Evidence boundary |
| --- | --- | --- |
| MCP protocol and closed tool schemas | **Supported** | contract and subprocess tests |
| Codex/Claude/OpenCode manifests and static configuration | **Supported local-only** | repository validators/tests |
| Generic read-only Skill bundle | **Supported local-only** | source/hash/budget/test manifest verification |
| Autonomous Knowledge Sink domain/MCP contract | **Supported in v0.12.0, explicit opt-in** | contract, capability, idempotency, scope, rate, integrity, and stdio tests |
| Living Wiki Compile Skill and compiler Sink operations | **Current working-tree implementation, explicit opt-in** | shared coordinator, closed Plan, fake-Agent E2E and least-privilege host examples |
| No-model Codex plugin lifecycle | **Supported local-only** | official CLI, isolated local-Git marketplace, v0.5→v0.7 upgrade, enable/disable, remove/re-add and dual-product survival |
| No-model Claude Code plugin lifecycle | **Supported local-only** | official CLI strict validation, discovery, install, enable/disable, v0.5→v0.7 upgrade, removal and isolation |
| No-model OpenCode adapter lifecycle | **Supported local-only** | official CLI resolved config, agent/skill discovery, MCP handshake, enable/disable, local adapter upgrade/removal and isolation |
| Obsidian CLI bridge and bundle | **Source candidate / local-only** | strict parsers, picker flow, npm test/check/build/bundle; real desktop E2E pending |
| Tolaria external MCP bridge | **`integration_limited`** | exact v2026-07-22 source contract and real local CLI harness; missing third-party product UI seam |
| Real model/session tasks on all hosts | **External verification pending** | competitive evidence only; no-model lifecycle is not task acceptance |

The compiler workflow and grant boundary are specified in
[`LIVING_WIKI_COMPILER.md`](LIVING_WIKI_COMPILER.md). The default plugin remains read-only. A host
must separately configure `knowledge_sink` with an owner-created grant limited to compilation
operations, and the Agent must load the applicable split Skill, normally `deeplaw-compile-source`.
The scheduled `compile-living-wiki` wrapper is compatibility-only. The opt-in real-host
harness records unavailable model tasks as `not_executed`.

The retained v0.7.0 host report is historical evidence scoped to official-CLI configuration,
manifest, lifecycle, and MCP stdio handshake without a model or API key. The v0.9 release gate
reruns the same no-model matrix from the exact release wheel, but neither result is reported as
model/task acceptance. Real recall/context/verify, Explain boundary,
restricted exclusion, proposal/feedback handling, and inactive-session tasks remain in the separate
competitive evidence program.

## Stable boundary

The Legal Pack process command is:

```text
deeplaw mcp --stdio
```

The common server key is `deeplaw`. Hosts add different prefixes to MCP tools,
so the visible name can differ, but the server-level leaf name must remain
exactly `law_support`. For example, OpenCode renders it as
`deeplaw_law_support`. Host namespacing does not create a second public tool.

`law_support` routes nine read-only operations under the v0.9 v3 contract (the historical v0.7
package retains the frozen v2 surface):

| Operation | Purpose | Required selector |
| --- | --- | --- |
| `search` | Return at most five evidence cards | `query` |
| `get` | Read one selected exact segment | `segment_id` |
| `verify` | Verify one evidence receipt | `segment_id`, `receipt_id` |
| `release_info` | Inspect the active immutable release | none |
| `private_search` | Search the separate user-private legal-reference snapshot | `query` |
| `private_get` | Read one selected private segment | `segment_id` |
| `private_verify` | Verify one private snapshot receipt | `segment_id`, `receipt_id` |
| `private_info` | Inspect the private snapshot | none |
| `federated_context` | Compile separately admitted official/private/Agent interpretation partitions | `query`, `confirm_no_case_data=true` |

No host adapter may expose a separate write, upload, memory,
reindex, delete, activation, administration, case, or chat tool. Build and
activation remain offline CLI administration, outside the Agent surface. The
private operations are read-only routes on the same leaf; private add/delete
remain local CLI administration.

The MCP handshake is a release gate: `tools/list` must contain one item, and its
leaf name must be `law_support`. Treat zero tools, a renamed tool, or a second
tool as a deployment failure rather than silently continuing.

## Runtime prerequisite

Install the `deeplaw` executable into the environment used to launch the Agent
host. The signed official catalog contains PDFs, so a machine that performs its
first official install or any official update needs the full document build
runtime plus PDF rendering, OCR, and Simplified Chinese language data:

```bash
uv tool install '.[document-engine]'

# macOS (Homebrew)
brew install poppler tesseract tesseract-lang

# Debian / Ubuntu
sudo apt-get update
sudo apt-get install -y poppler-utils tesseract-ocr tesseract-ocr-chi-sim

deeplaw --version
deeplaw document-engine setup
deeplaw document-engine status
deeplaw-document-engine --version
pdftoppm -v
tesseract --list-langs | grep -x 'chi_sim'
deeplaw official install
deeplaw official status
```

`document-engine setup` is an explicit operator action, not an Agent or MCP
operation. It fetches one pinned model revision and writes configuration only
after the closed 15-file manifest passes size and SHA-256 verification. Build,
private ingestion, MCP startup, and query paths never download models or honor
upstream model/configuration environment overrides.

Contributors who intentionally want live source edits can instead use
`uv tool install --editable '.[document-engine]'`; a normal user updates checkout
code with `uv tool install --force --reinstall-package deeplaw '.[document-engine]'`.
A machine that only
reads an already-built immutable release may use the lightweight
`uv tool install .`, but that installation cannot perform the signed official
PDF catalog build. A project-only `uv sync` is insufficient when the Agent
launches the plugin from another working directory unless its environment also
exposes that project's `.venv/bin`.

The MCP process inherits its environment. Point it at an immutable database with
`DEEPLAW_DB`, or point `DEEPLAW_HOME` at a directory containing `ACTIVE` and the
corresponding `releases/<release-id>/deeplaw.sqlite3`. Do not put a machine-local
database path in a committed plugin manifest. With neither override, every host
uses the same user-level `~/.deeplaw` home, so a wheel install does not depend on
the checkout or current working directory.

Before connecting a host, verify the selected release:

```bash
deeplaw doctor
deeplaw mcp --help
```

Running `deeplaw mcp --stdio` directly waits for MCP messages on standard input;
that is expected, not a startup hang.

The plugin never downloads or mutates a corpus in the background. Official
updates require an explicit `deeplaw official update`; private legal-reference
imports require `deeplaw private add --confirm-no-case-data`. An MCP process
pins both available scopes at startup. Restart it after an official update or a
private mutation; after either managed epoch changes, the old process rejects
later reads in that scope when its pinned epoch no longer matches.

The Knowledge Asset process selects one vault through
`DEEPLAW_KNOWLEDGE_VAULT` or `--vault`. It opens the vault read-only for each
operation, verifies its closed identities and audit chains, and never mutates
knowledge. An untouched v0.7 Vault advertises the v1 reviewed-asset contract;
an autonomous v0.9 Vault advertises v3 with source-derived and Agent-derived
partitions. Restart is not required merely to observe a later committed
revision, but a previously compiled Capsule remains bound to its recorded
revision/audit head.

## Explicit invocation policy

DeepLaw is an optional capability, not a permanent legal persona. Ordinary
Agent work must not receive legal retrieval merely because a workspace contains
a case or a string resembles a legal term.

Positive explicit triggers include a user choosing the DeepLaw skill or adapter
and asking to retrieve or verify a Chinese legal source, document number,
article, historical version, effective date, statutory element, legal issue, or
citation.

These are negative triggers and must not invoke DeepLaw by themselves:

- coding, SQL, statistics, dashboards, and ordinary data analysis;
- summarizing, extracting, OCRing, translating, or rewriting DOCX/PDF/text;
- UI, session, attachment, SQLite, DuckDB, or project-management work;
- searching or storing private case evidence and chat history; the DeepLaw
  private scope contains legal references only;
- filenames, columns, labels, or prose containing `诈骗`, `案件`, `法务`,
  `fraud`, `risk`, or another isolated domain word.

If the user explicitly invokes DeepLaw with only a topic such as `诈骗`, use a
small navigation response. Do not reinterpret that as permission for a broad
vector dump or a complete legal memorandum.

## Codex

The Codex plugin is rooted at `plugins/deeplaw`:

- `.codex-plugin/plugin.json` provides plugin metadata;
- `.mcp.json` starts `deeplaw mcp --stdio`;
- `skills/research-chinese-law/SKILL.md` is the shared workflow;
- `skills/research-chinese-law/agents/openai.yaml` sets
  `policy.allow_implicit_invocation: false`.

The repository's DeepLaw marketplace entry can be used from the Codex plugin
browser during development. Install DeepLaw, start a new task, and invoke it
explicitly:

```bash
codex plugin marketplace add /absolute/path/to/DeepLaw
codex plugin add deeplaw@deeplaw
```

The reproducible lifecycle diagnostic uses a fresh temporary `CODEX_HOME`, `HOME`, and XDG roots,
does not seed user configuration or credentials, never starts a model/API request, and retains only
sanitized parsed state plus hashes and byte counts for the raw CLI streams:

```bash
uv run --frozen python benchmarks/hosts/run_codex_plugin_smoke.py \
  --codex /absolute/path/to/codex \
  --output dist/codex-plugin-smoke.json
```

The checked-in historical v0.7 rerun from 2026-07-29 is
[`benchmarks/hosts/codex-plugin-smoke-2026-07-29.json`](../benchmarks/hosts/codex-plugin-smoke-2026-07-29.json).
It discovered both plugins, installed both, proved the untouched product remained installed while
the other was removed and re-added, compared every cached plugin file to its source bytes twice,
and finished with no installed plugin. The report sets `scope=plugin-lifecycle-only`,
`full_host_acceptance=false`, and `claim_eligible=false`. It did not enforce OS network isolation,
start MCP/model sessions, or test task activation, session tool discovery, recall, context, verify,
Explain, restricted exclusion, read-only behavior, proposal/feedback artifacts, or inactive
zero-impact.

```text
$research-chinese-law 核验《中华人民共和国刑法》某条在 2020-06-01 的有效版本。
```

Codex's `allow_implicit_invocation: false` is the hard selection gate: the skill
is available for explicit use but is not injected into ordinary model context.

That policy gates the Skill, not the lifecycle of a bundled MCP server. A Codex
version that eagerly registers installed-plugin MCP tools can still place the
single `law_support` schema in its tool catalog. No legal text or search result
is injected until the tool is called, and the Skill plus server descriptions
require explicit use, but that is not a hard per-tool permission boundary. A
deployment requiring zero DeepLaw schema on non-legal turns must keep the plugin
disabled outside legal-research sessions or use a host profile that can hide the
tool. The one-tool surface is the bounded compatibility fallback, not a claim of
zero overhead.

Official references:

- <https://learn.chatgpt.com/docs/plugins>
- <https://learn.chatgpt.com/docs/build-plugins>

## Claude Code

Each plugin root contains its own `.claude-plugin/plugin.json`, while the
repository-level `.claude-plugin/marketplace.json` lists the two products
separately. Validate and install from a local checkout:

```bash
claude plugin validate ./.claude-plugin/marketplace.json --strict
claude plugin marketplace add /absolute/path/to/DeepLaw
claude plugin install deeplaw@deeplaw
claude plugin install deeplaw-knowledge-os@deeplaw
```

For a one-session development load without marketplace installation:

```bash
claude plugin validate ./plugins/deeplaw --strict
claude --plugin-dir ./plugins/deeplaw
```

Invoke the shared skill explicitly:

```text
/deeplaw:research-chinese-law 核验该法条的文号、条次和效力期间。
```

The plugin manifest describes components but does not control whether Claude
Code enables the plugin. Installation scope and enable/disable state are owned
by Claude Code settings and plugin-management commands. A deployment requiring
default-off behavior must keep the plugin uninstalled or disabled outside an
explicit legal-research session and verify that host state; the manifest alone
is not an enablement or permission boundary.

After an explicit enable or `--plugin-dir` development load, the shared skill's
description and workflow still require explicit invocation. Claude Code does
not consume Codex's `agents/openai.yaml`; therefore deployments that require a
hard per-skill runtime gate must enforce it through managed Claude settings or
keep the plugin disabled outside explicit legal-research sessions. Do not claim
that a prose description is a security boundary.

Official references:

- <https://code.claude.com/docs/en/plugins>
- <https://code.claude.com/docs/en/plugins-reference>

## OpenCode

OpenCode uses native configuration rather than the Codex/Claude plugin
manifests. Merge `adapters/opencode/opencode.jsonc` into the user's existing
`opencode.jsonc`; do not overwrite unrelated provider, model, or permission
settings. Install the maintained shared Skill and the dedicated adapter agent:

```bash
mkdir -p .opencode/skills/research-chinese-law .opencode/agents
cp plugins/deeplaw/skills/research-chinese-law/SKILL.md \
  .opencode/skills/research-chinese-law/SKILL.md
cp adapters/opencode/agents/deeplaw.md .opencode/agents/deeplaw.md
```

The sample configuration starts the local MCP server but denies `deeplaw_*`,
the skill, and the `deeplaw` Task target to ordinary agents. The dedicated
adapter reverses only the two permissions it needs: `research-chinese-law` and
`deeplaw_law_support`. Because task permission denial removes the subagent from
the model's Task description while still permitting a user's direct mention,
invoke it explicitly:

```text
@deeplaw 核验这条司法解释在 2019-03-01 是否已施行，并给出来源定位。
```

Validate the merged configuration with the installed OpenCode version:

```bash
opencode debug config
opencode mcp list
opencode agent list
```

Official references:

- <https://opencode.ai/docs/mcp-servers>
- <https://opencode.ai/docs/agents>
- <https://opencode.ai/docs/skills>

## Optional Knowledge Asset adapter

The Knowledge Asset plugin is rooted at `plugins/deeplaw-knowledge-os` and must
be installed separately. Its current split Skills are explicit-only:

- Codex Skills: `$deeplaw-query`, `$deeplaw-compile-source`,
  `$deeplaw-verify-evidence`, `$deeplaw-refresh-synthesis`,
  `$deeplaw-navigate-wiki`, and `$deeplaw-promote-draft`
- Claude Code uses the same six packaged Skill names through its plugin namespace
- OpenCode agent: `@deeplaw-knowledge`

`use-knowledge-assets` and `compile-living-wiki` are scheduled compatibility
wrappers, not the current default path.

Codex development install:

```bash
codex plugin marketplace add /absolute/path/to/DeepLaw
codex plugin add deeplaw-knowledge-os@deeplaw
```

Claude Code development load:

```bash
claude plugin validate ./plugins/deeplaw-knowledge-os --strict
claude --plugin-dir ./plugins/deeplaw-knowledge-os
```

OpenCode uses
[`adapters/opencode/knowledge-os.jsonc`](../adapters/opencode/knowledge-os.jsonc)
and
[`adapters/opencode/agents/deeplaw-knowledge.md`](../adapters/opencode/agents/deeplaw-knowledge.md).
Merge the sample rather than overwriting the user's configuration, then copy
the skill and agent:

```bash
mkdir -p .opencode/skills/deeplaw-query .opencode/agents
cp plugins/deeplaw-knowledge-os/skills/deeplaw-query/SKILL.md \
  .opencode/skills/deeplaw-query/SKILL.md
cp adapters/opencode/agents/deeplaw-knowledge.md \
  .opencode/agents/deeplaw-knowledge.md
```

After autonomous migration, the recommended `knowledge_support` read path is:

| Operation | Purpose |
| --- | --- |
| `search` / `recall` | return bounded source-derived and autonomous partitions without merging Authority |
| `get` | read one exact active non-restricted `knowledge_id` or legacy `asset_id` |
| `query` | run default Query Plan v6 statement selection, duty coverage and targeted evidence completion |
| `context` | compile the bounded compatibility Knowledge Capsule through the shared domain service |
| `wiki` | read exact pages, indexed links, local graphs, kinds and recent changes |
| `source` | read exact admitted Source Revisions and fragments |
| `verify` | verify object/source binding, current usability, both event chains, and state reconciliation |
| `inspect` | inspect sanitized readiness, scoped counts, integrity, and legacy compatibility state |
| `lineage` | read bounded immutable revision metadata for one Knowledge Object |
| `graph` | read bounded canonical relation revisions after endpoint admission |
| `explain` | return hashed query plans, admission/selection receipts, gaps, and budgets |
| `identity_lookup` | return bounded Concept/Entity identity candidates without silently merging ambiguity |
| `gaps` | return scope- and sensitivity-bounded semantic knowledge gaps without leaking other partitions |

`context` requires `confirm_no_case_data=true` because task and goal text become
provider-visible Capsule data. A host may send that confirmation only after keeping
client/case facts, chats, identifiers, and attachments out of the request.

The default plugin has no `remember`, relation mutation, feedback write,
`approve`, `import`, delete, shell, web, or case operation. Host configuration
is not permission to copy client or case data into a vault.

This read-only guarantee covers the DeepLaw MCP surface. A host must not give
the same Agent a separate same-owner shell or filesystem route to
`deeplaw knowledge approve`, private-library administration, official-library
administration, or `~/.deeplaw`. Use host tool policy/path denial or a separate
OS identity for the MCP process when this is a security boundary.

The Knowledge Asset MCP process can initialize and advertise its one closed
read-only tool before a vault exists. A read then returns a sanitized
unavailable error; it does not reveal the configured path, create a vault, or
silently select another vault. This prevents an optional plugin with incomplete
local setup from breaking host MCP discovery.

Every general result declares `legal_authority=false`; authoritative Chinese
legal research must use the separate `law_support` plugin. Search returns rank
and a deterministic hit reason, not a confidence probability. Context items
identify a lexical or bounded reviewed-relation selection reason, and
open-question actions contain only a stable object URI.

### Separately enabling the Knowledge Sink

Owner administration first creates an exact grant:

```bash
deeplaw knowledge sink enable \
  --vault /absolute/owner/vault \
  --writer-id codex-local \
  --scope project \
  --max-sensitivity private \
  --operation remember \
  --operation reflect \
  --operation record_feedback
```

Only then may the owner add a separate host MCP entry whose command is:

```text
deeplaw knowledge sink mcp --vault <owner-vault> --grant-id <exact-grant-id> --stdio
```

Do not commit a Vault path, grant ID, or capability token. Never add this
server to the default plugin manifest. The server exposes exactly one
`knowledge_sink` leaf; its closed request requires an idempotency key and
`confirm_no_case_data=true`. A model, retrieved page, Skill, or router cannot
run `sink enable`, choose a broader grant, or add owner-only operations.

## Client and case workspaces remain outside DeepLaw

DeepLaw does not store client or case projects. Any separately authorized host bridge must
preserve these invariants:

1. Register an optional DeepLaw stdio MCP profile, disabled for normal tasks.
2. Start `deeplaw mcp --stdio` only after an explicit legal-research action or
   an explicitly selected legal mode. Do not use a free-form classifier as the
   sole activation gate.
3. During MCP initialization, allowlist exactly the `law_support` leaf tool and
   reject any other DeepLaw tool before its schema reaches the model.
4. Add the tool schema only to the legal-research turn. Preserve the host's
   stable system prefix and ordinary data tools for every other turn.
5. Keep private case documents, facts, chats, embeddings, SQLite state, and
   DuckDB data inside the case project. Send only a minimal de-identified legal
   issue to the official DeepLaw scope. The optional DeepLaw user-private scope
   is for legal references, not a case store.
6. Keep the two-stage pattern: bounded `search`, then exact `get` and selective
   `verify`. Do not place broad retrieval results in the main context.
7. Fail closed. If the process, release, receipt, or version check fails, report
   that DeepLaw is unavailable. Do not fall back silently to model memory, web
   search, generated topic-page text, or case-private retrieval.
8. Log only operational metadata needed for diagnostics. Do not log raw legal
   queries when they may encode case facts.

This keeps DeepLaw additive: legal-source research becomes available when
requested without changing the host's behavior, context size, or tool choice
on non-legal data work.

## Adapter validation checklist

Before publishing an adapter release:

1. Parse both products' Codex/Claude manifests, `.mcp.json`,
   `agents/openai.yaml`, and OpenCode samples.
2. Run the Codex plugin and Skill validators for both plugin roots, then run the isolated Codex
   plugin-lifecycle smoke and verify its schema/digest/source inventories.
3. Run `claude plugin validate` for both plugin roots when Claude Code is
   installed.
4. Validate the OpenCode config and agent with the target OpenCode release.
5. Start the stdio process and assert `tools/list == [law_support]` by leaf name.
6. Test a positive explicit legal query and every negative-trigger family.
7. Confirm no request writes the release database or includes private case
   identifiers.
8. Confirm ordinary code/data tasks do not load the Skill or call DeepLaw. On a
   host with tool permissions, also confirm the schema is hidden; on an eager
   plugin host, confirm that at most the single `law_support` schema is present
   and no legal source content is retrieved.
9. For the optional Knowledge Asset plugin, assert
   `tools/list == [knowledge_support]`, restricted/inactive assets are blocked,
   local paths are absent, and no persistent write operation exists.
