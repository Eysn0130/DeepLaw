# DeepLaw Agent Adapters

DeepLaw provides thin local configuration adapters for Codex, Claude Code and OpenCode, plus
limited editor integrations for Obsidian and Tolaria. Adapters serve three product roles on one
shared governed kernel: Task Continuity / Governed Project Knowledge, Source-native Evidence
Library, and Living Wiki. The Context Compiler is shared; adapters do not duplicate retrieval,
identity, governance, persistence, or Host runtime logic. Mutation is a separate capability and
must not be collapsed into a read surface:

| Product | Plugin | Process | Single leaf |
| --- | --- | --- | --- |
| Source-native Evidence Library / Legal Pack | `deeplaw` | `deeplaw mcp --closed-environment --stdio` | `law_support` |
| Task Continuity, Governed Project Knowledge and Living Wiki read surface | `deeplaw-knowledge-os` | `deeplaw knowledge mcp --closed-environment --stdio` | `knowledge_support` |
| Autonomous mutation (not registered by default) | owner host config | `deeplaw knowledge sink mcp --closed-environment --grant-id … --stdio` | `knowledge_sink` |

This document describes adapter behavior only. Corpus building, release
governance, and retrieval internals are separate concerns.
The default/Advanced/Compatibility/Experimental/Retire Candidate classification is frozen in
[`../governance/product-surface-manifest.v1.json`](../governance/product-surface-manifest.v1.json).

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
| Obsidian CLI bridge and bundle | **Source candidate / local-only** | exact-candidate synthetic macOS load/verify/rename/edit/reconcile/conflict-recovery seam executed; Human/blind and broader qualification pending |
| Tolaria external MCP bridge | **`integration_limited`** | exact `v2026-08-11` source/hash contract and local CLI harness; Desktop/UI seam not executed |
| Real model/session tasks on all hosts | **Pending under machine-only profile** | Active profile is `machine_evaluated_no_human_attestation`; required Codex/OpenCode runs and mapped Core evidence remain source-specific `not_executed` until a fresh exact candidate/input/receipt binding; historical candidate evidence is not Human Gold or legal attestation |

The compiler workflow and grant boundary are specified in
[`LIVING_WIKI_COMPILER.md`](LIVING_WIKI_COMPILER.md). The default plugin remains read-only. A host
must separately configure `knowledge_sink` with an owner-created grant limited to compilation
operations, and the Agent must load the applicable split Skill, normally `deeplaw-compile-source`.
The scheduled `compile-living-wiki` wrapper is compatibility-only. The opt-in real-host
harness records unavailable model tasks as `not_executed`. The Pass 10 receipts under
[`../benchmarks/hosts/evidence/`](../benchmarks/hosts/evidence/) remain historical candidate
evidence only; the current invalidation is recorded in
[`V0_13_PASS10_CURRENT_DISPOSITION.md`](V0_13_PASS10_CURRENT_DISPOSITION.md).
The current Codex token-attribution failure disposition is recorded in
[`V0_13_PASS11_TOKEN_ATTRIBUTION_DISPOSITION.md`](V0_13_PASS11_TOKEN_ATTRIBUTION_DISPOSITION.md).
The current OpenCode continuity failure disposition is recorded in
[`V0_13_PASS11_OPENCODE_DISPOSITION.md`](V0_13_PASS11_OPENCODE_DISPOSITION.md).
The current editor/Wiki/scale evidence boundary is recorded in
[`V0_13_PASS11_WIKI_EVIDENCE_DISPOSITION.md`](V0_13_PASS11_WIKI_EVIDENCE_DISPOSITION.md).

The retained v0.7.0 host report is historical evidence scoped to official-CLI configuration,
manifest, lifecycle, and MCP stdio handshake without a model or API key. The v0.9 release gate
reruns the same no-model matrix from the exact release wheel, but neither result is reported as
model/task acceptance. Real recall/context/verify, Explain boundary,
restricted exclusion, proposal/feedback handling, and inactive-session tasks remain in the separate
competitive evidence program.

## Current `knowledge_support` Provider advertisement

The current public advertisement is exactly
`contracts/knowledge-support.input.v7.schema.json` plus
`contracts/knowledge-support.output.v6.schema.json`. It exposes only the read operations `query`,
`context`, and `explain`. Input v1-v6 and output v1-v5 remain internal compatibility contracts for
existing callers and persisted receipts; their broader historical operation inventories are not
current Provider tools. Provider bytes contain only bounded admitted context and safe authority,
provenance, freshness and Gap fields. Paths, session/task hashes, internal receipt or selection
identity, raw logs, transcript, reasoning, Secret material and unadmitted content are excluded.

## Stable boundary

The production Legal Pack launcher command is:

```text
deeplaw mcp --closed-environment --stdio
```

The common server key is `deeplaw`. Hosts add different prefixes to MCP tools,
so the visible name can differ, but the server-level leaf name must remain
exactly `law_support`. For example, OpenCode renders it as
`deeplaw_law_support`. Host namespacing does not create a second public tool.

`law_support` routes thirteen read-only operations under the current input/output v4 contract (the
historical v1-v3 contracts remain compatibility surfaces):

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
| `capabilities` | Read deterministic evidence capabilities for one exact segment | `segment_id` |
| `challenge_trace` | Build a bounded deterministic Authoritative Pack challenge trace | `query` |
| `challenge_get` | Fetch one exact locally retained challenge trace | `trace_id` |
| `challenge_replay` | Replay and verify a supplied challenge trace | `trace` |

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

The raw MCP process inherits its caller environment, so every official static and generated Host
configuration uses the built-in fixed-target `--closed-environment` launcher. It accepts only
`law_support`, `knowledge_support`, or an explicitly granted `knowledge_sink`; it is not a generic
command runner. The child receives a small portable process allowlist, isolated `HOME`, Windows
`USERPROFILE`, XDG and temporary roots, and only explicit DeepLaw data/task settings. `CODEX_HOME`,
Host plugin/hook state, provider API keys, `.env` settings, credential paths, and the ambient user
profile are absent. Point the launcher at an immutable database with
`DEEPLAW_DB`, or at a directory containing `ACTIVE` and the corresponding
`releases/<release-id>/deeplaw.sqlite3`. Do not put a machine-local database or credential path in a
committed plugin manifest. With neither DeepLaw data override, every host
uses the same user-level `~/.deeplaw` home, so a wheel install does not depend on
the checkout or current working directory.

Before connecting a host, verify the selected release:

```bash
deeplaw doctor
deeplaw mcp --help
```

Running either raw `deeplaw mcp --stdio` for local diagnostics or the production closed launcher
waits for MCP messages on standard input;
that is expected, not a startup hang.

The plugin never downloads or mutates a corpus in the background. Official
updates require an explicit `deeplaw official update`; private legal-reference
imports require `deeplaw private add --confirm-no-case-data`. An MCP process
pins both available scopes at startup. Restart it after an official update or a
private mutation; after either managed epoch changes, the old process rejects
later reads in that scope when its pinned epoch no longer matches.

The closed Knowledge Asset launcher and Host Connect use the same resolver for ancestor
symlink/junction/reparse rejection, owner, permission and Vault-identity checks. Host Connect binds
the owner-selected path to its opaque Vault ID in a private owner-local DeepLaw configuration;
generated Host configuration contains only `--expected-vault-id`, never a Vault path or binding
file location. The launcher resolves that ID through the private binding and the raw child
revalidates the same expected ID after process creation, closing the parent/child TOCTOU seam. An
explicit local `--vault` remains an owner diagnostic/compatibility input, not a production Host
configuration. The process opens the vault read-only for each
operation, verifies its closed identities and audit chains, and never mutates
knowledge. An untouched v0.7 Vault and an autonomous Vault may retain historical
compatibility contracts internally, but the current Host advertisement remains
input v7/output v6 with only `query`, `context`, and `explain`. Restart is not
required merely to observe a later committed revision, but a previously compiled
Capsule remains bound to its recorded revision/audit head.

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
- `.mcp.json` starts `deeplaw mcp --closed-environment --stdio`;
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

### Owner-side Host connection plan

The Basic CLI journey exposes one real, read-only connection preflight for the supported static
Host configuration shapes:

```bash
deeplaw knowledge host connect --host codex --vault ./vault
deeplaw knowledge host connect --host claude-code --vault ./vault
deeplaw knowledge host connect --host opencode --vault ./vault
```

Static Host configuration is task-neutral. For task continuity, DeepLaw normalizes the chosen
project/task labels, derives only digests for project, task lineage, repository and worktree, and
recomputes the base revision and bounded dirty snapshot from the explicitly selected Git worktree.
An opaque task handle remains an optional exact optimization; ordinary recovery locates the route
from project + task text + current workspace and returns a Gap if more than one route remains:

```bash
deeplaw knowledge task start --vault ./vault \
  --project DeepLaw --task 'Finish the selected task.' --workspace .
deeplaw knowledge task locate --vault ./vault \
  --project DeepLaw --task 'Finish the selected task.' --workspace .
deeplaw knowledge task timeline --vault ./vault \
  --project DeepLaw --task 'Finish the selected task.' --workspace .
deeplaw knowledge task resume --vault ./vault \
  --project DeepLaw --task 'Finish the selected task.' --workspace .
```

The command verifies that the selected Vault is ready, canonically valid, and has the autonomous
core installed. It also executes one fixed, bounded, no-write, no-model Context health probe and
verifies that the audit head is unchanged. That attestation covers only the fixed internal probe;
it does not attest a future user task or goal, a real Host launch, or MCP registration. Every later
caller-supplied Context/ingestion/mutation request still requires its own explicit
`confirm_no_case_data` confirmation. The plan distinguishes compiled Knowledge, a source-only
honest Gap, and an empty honest Gap; an uncallable read seam is blocked rather than reported ready.
It then validates and prints a
[`host-connect-plan/v2`](../contracts/host-connect-plan.v2.schema.json) document containing only a
`knowledge_support` stdio configuration. Codex direct setup is represented as TOML for either
`~/.codex/config.toml` or trusted-project `.codex/config.toml`, together with the equivalent
`codex mcp add ...` command and `codex mcp list` verification command. The separately named
`codex_plugin_manifest` is JSON for the plugin-root `.mcp.json`; the `.codex-plugin/plugin.json`
manifest points to it with `"mcpServers": "./.mcp.json"`. It is not Codex direct configuration.
Claude Code receives its actual `mcpServers` JSON shape. OpenCode receives a local
MCP command array for `opencode.json`/`opencode.jsonc`, wildcard deny, and the exact read leaf allow.
`merge_required=true`: DeepLaw does not write Host configuration, install a Host, manage Host
authentication or runtime state, or enable the separate `knowledge_sink` process. It does perform
one narrowly scoped owner-local DeepLaw configuration write that binds the opaque Vault ID to the
selected path; the plan reports that write explicitly. The plan itself is path-free, binds
`--expected-vault-id`, and uses the fixed closed launcher. Static configuration cannot embed a task
handle. At Host lifecycle time, a Host-local official session event supplies its current cwd plus
an optional Host session/fork hint; the adapter passes the explicit workspace and DeepLaw
re-resolves the Vault/project/task/workspace binding. Host route result v2 separates the stable
project/repository/worktree/task-lineage/session route from the mutable base revision and dirty-state
snapshot. A normal edit therefore preserves the route, while checkpoint admission independently
returns `workspace_diverged` or `stale_checkpoint` and withholds the old checkpoint. The frozen v1
result contract remains valid for retained receipts. The launcher never falls back to ambient
`Path.cwd()`. The compatibility `--task-binding` form remains available for existing callers. A
configured launcher binding is a fixed read boundary: a call cannot replace it with another line.

Owner-side direct verification remains explicit:

```bash
codex mcp list
claude mcp list
opencode mcp list
```

This is an owner-side setup artifact, not provider-visible context. The owner-selected local Vault
path remains only in the private DeepLaw owner binding and child environment; it never appears in
the generated plan. Qualification
receipts, Provider Capsules, logs, screenshots, and public support bundles also omit it. Adapters
continue to delegate retrieval, admission, governance, and persistence to the shared domain
services.

Codex and OpenCode continuity consume only the official lifecycle seams documented in their
adjacent adapter READMEs, verified lineage, and cwd. Host IDs are untrusted routing hints, not
DeepLaw identity or Authority: DeepLaw rebinds them to the owner-selected Vault, project, task, and
workspace. Neither adapter stores transcript, hidden reasoning, authentication material, or Host
private memory, and neither implements a second Agent runtime.

OpenCode provider authentication is outside the DeepLaw runner. A repository-external,
owner-only credential broker may read its owner-only dotenv and launches only the exact OpenCode
Host process with the required provider variable. The DeepLaw runner receives a separate
owner-only launcher path and never receives the dotenv path or Secret value; DeepLaw CLI/MCP and
scorer processes use closed environments without that variable. Formal qualification additionally
requires the external process receipt to bind the broker executable, Host executable, PID/process
tree, environment-name allowlist, inputs/outputs, timestamps, and exit status. A launcher hash or
self-reported JSON alone is not isolation evidence.

The formal candidate Host runner receives no Human Gold, machine-reference labels, scorer source,
or dotenv path. Codex delivery is observed from the public App Server `hook/completed` entry and
must exactly match an independent read-only resolver result; OpenCode delivery is observed by the
exact candidate project plugin at `experimental.chat.system.transform` and matched the same way.
Both paths retain only delivery hashes, byte/counter fields, status and Gap codes. A formal turn
must make zero Provider-side continuity tool calls. Reference freezing and scoring run later in
separate processes and remain machine evaluation unless an independent human attestation actually
exists.

The disabled sidecars, owner-only enable/disable path, read-only boundary, and exact event
mappings are documented in `adapters/codex/README.md` and `adapters/opencode/README.md`; static
tests are not real Host receipts.

The shipped MCP caller inventory is closed as follows:

- static Codex/Claude plugin manifests for `deeplaw` and `deeplaw-knowledge-os`, plus the three
  OpenCode samples, invoke the fixed launcher;
- Host Connect v2 and Tolaria generated configurations use the shared resolver/config builder;
- the Obsidian display-only MCP setting, Codex plugin smoke, no-model registration check and editor
  integration harness show or exercise the same production command;
- current Codex/OpenCode continuity and token-attribution runners, and retained Pass 13
  compatibility runners, delegate their MCP child to the production launcher;
- raw `law_support`, `knowledge_support`, and `knowledge_sink` stdio commands remain explicit
  owner diagnostic/compatibility seams and are not emitted into production Host configuration.

For owner/Host compilation orchestration, the command
`deeplaw knowledge compile handoff --source-revision-id <exact-id>` produces a read-only receipt
that binds the existing compiler profile and the public
`knowledge_support`/separate `knowledge_sink` saga. It does not include a Grant, call a model, merge
read and write leaves, or add another coordinator.

Codex plugin development install uses the current local marketplace seam. Only `plugin.json` is
stored below `.codex-plugin/`; bundled MCP configuration is the plugin-root `.mcp.json`:

```bash
codex plugin marketplace add /absolute/path/to/DeepLaw
codex plugin add deeplaw-knowledge-os@deeplaw
```

Historical status — Pass 14 only: the no-model lifecycle check used a fresh temporary HOME,
`CODEX_HOME` and XDG roots,
discovered both local plugins, installed/removed/re-added them, and compared the cached plugin bytes
with their source bytes. It passed with `codex-cli 0.147.0-alpha.1.2`, but remains
`full_host_acceptance=false` and `claim_eligible=false`: no model or MCP session was started.
The subsequent isolated ChatGPT-login preflight failed closed, so real Codex diagnostic and
continuity qualification are `not_executed`. No installed OpenCode binary was available for its
required version/config preflight, so its diagnostic and qualification were also `not_executed`.
This paragraph does not describe current Pass 17 evidence; see
[`V0_13_PASS17_DISPOSITION.md`](V0_13_PASS17_DISPOSITION.md). Pass 19 adds local no-model closed
launcher and task-continuity regressions only and does not upgrade real-Host qualification.

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

After autonomous migration, the local compatibility inventory is:

The public Provider advertisement is only input v7/output v6 with `query`, `context`, and
`explain`. The operations below are local CLI/Python/internal compatibility calls and are not
additional advertised Provider tools.

| Operation | Purpose |
| --- | --- |
| `search` / `recall` | return bounded source-derived and autonomous partitions without merging Authority |
| `get` | read one exact active non-restricted `knowledge_id` or legacy `asset_id` |
| `query` | run default Query Plan v6 statement selection, duty coverage and targeted evidence completion |
| `context` | compile default Query Plan v6 through the shared domain assembler into local Capsule v3 plus bounded Provider v2 |
| `wiki` | read exact pages, indexed links, local graphs, kinds and recent changes |
| `source` | read exact admitted Source Revisions and fragments |
| `verify` | verify object/source binding, current usability, both event chains, and state reconciliation |
| `inspect` | inspect sanitized readiness, scoped counts, integrity, and legacy compatibility state |
| `lineage` | read bounded immutable revision metadata for one Knowledge Object |
| `graph` | read bounded canonical relation revisions after endpoint admission |
| `explain` | return hashed query plans, admission/selection receipts, gaps, and budgets |
| `identity_lookup` | return bounded Concept/Entity identity candidates without silently merging ambiguity |
| `gaps` | return scope- and sensitivity-bounded semantic knowledge gaps without leaking other partitions |

Autonomous `context` defaults to Query Plan v6 for Python, both Knowledge CLI Context commands,
and MCP `operation=context`. `query_target`, `applicable_duties`, `projection`, `graph_hops`,
`retrieval_mode`, and integrity-selected canonical lexical fallback are explicit v6 plan controls;
they are not silently discarded. The local Capsule v3 is capped at 262,144 bytes, while Provider
v2 content is capped at 65,536 bytes and receives only bounded Statement/evidence data plus the
opaque `receipt_id`. The local Query Trace is bounded, redacted, non-persistent, and retained only
after Provider and outer response validation. Explicit `query_plan_version=5` is compatibility-only:
Python/CLI retain local Capsule v2, and MCP retains output/v3 with Capsule v2/Query Plan v5
semantics. The legacy `deeplaw recall` command remains the `retrieval_fabric` path and is not a v6
Context alias. Ordinary query/context calls are read-only and never append the Canonical Ledger.

For current advertised `query` and `context`, the MCP unstructured `content` text is exactly the canonical JSON of
the nested Provider Capsule projection counted by `delivery.provider_content_bytes`. The validated
`structuredContent` retains the complete local output contract for Host/application inspection;
its outer Authority boundary, delivery record, and receipt wrapper are not duplicated into the
Provider text block. Host observations must hash and count the actual `content` text rather than
substituting the larger structured result or a locally reconstructed payload.

`deeplaw knowledge context` is the single recommended Agent entry. `knowledge query` remains an
owner/operator diagnostic surface; `deeplaw recall`, `knowledge recall`, and autonomy aliases stay
compatibility or operator surfaces pending a consumer inventory. Working checkpoints are routed
through the shared Host-neutral identity normalizer before ordinary content discovery. The route
uses owner-registered opaque project, repository, stable-worktree, and task-line identifiers;
checkpoint base/dirty state is a separate snapshot. Absolute paths, branch names, current commits,
task text alone, and Host session/thread/memory references cannot create an identity, grant, or
authorization. Adapters may only map available Host hints into this shared seam.

For a binding-free cold request, exact task text can recover only when it resolves to one admitted
task line in the selected Vault. Multiple admitted lines return `task_line_ambiguous`; selecting
the newest is forbidden. The Provider sees neither candidate identities nor routing fields. This
local exact-match development path and static Codex/Claude/OpenCode adapter parity are implemented;
stable identity derivation and three-run real-Host cold-start qualification remain not executed.

New bound mutations use additive `knowledge-sink.input/v6`. Frozen input v2-v5 bytes remain
readable; an unbound legacy Run cannot silently ground a new default-v6 working checkpoint.
Current Run Artifact IDs are validated once at the domain commit seam as opaque, bounded,
non-path, non-Secret-shaped identifiers. Owner reconciliation creates a new bound Run and successor
Revision; adapters must not rewrite history or implement a second identity/reconciliation path.

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
deeplaw knowledge sink mcp --closed-environment \
  --expected-vault-id <opaque-vault-id> --grant-id <exact-grant-id> --stdio
```

The owner first establishes the private Vault-ID binding through `knowledge host connect`; the Host
configuration itself stays path-free. Do not commit a Vault path, grant ID, or capability token. Never add this
server to the default plugin manifest. The server exposes exactly one
`knowledge_sink` leaf; its closed request requires an idempotency key and
`confirm_no_case_data=true`. A model, retrieved page, Skill, or router cannot
run `sink enable`, choose a broader grant, or add owner-only operations.

### Explicit task checkpoint and restore chain

Query and Context are always read-only and never checkpoint. At an explicit successful task
boundary, a Host that has received an owner grant with only the needed operations performs two
separate `knowledge_sink` calls: `record_run(status=succeeded)` with the canonical task binding in
`run_metadata`, then `remember(kind=memory, memory_type=working, run_id=...)`. This reuses the
existing Run, Knowledge Revision, Ledger event and rebuildable checkpoint-route projection; it
does not store a transcript, hidden reasoning, complete log, authentication state or Host session.
Selective withdrawal uses the same separately authorized Sink's `forget` operation with the exact
current revision and owner reason.

Each new MCP process can deterministically recover the same admitted data-plane line when it
receives the same validated handle/binding. `task resume` and `task compaction` both reacquire a
fresh bounded Capsule and never copy a transcript. Fork mode is explicit: `continue-parent` keeps
the handle, while `child-task` creates a different lineage with an opaque parent digest.
Concurrent worktrees remain distinct. Changed base/dirty snapshots return `workspace_diverged` or
`stale_checkpoint`; wrong or ambiguous task lines and forgotten checkpoints return bounded Gaps
without guessing. Without an exact binding, only a unique task-text route may be recovered; that
compatibility path cannot prove the caller's current workspace snapshot and therefore is not a
substitute for Host binding. Fork merge/conflict reconciliation, transcript crawling, background
checkpointing and semantic whole-session restore are not implemented. Static MCP has no native
Host lifecycle metadata, so local start/resume/compaction tests are deterministic restart/data-plane
recovery only. Native Host start/resume/fork/compaction remains a real qualification claim.

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
