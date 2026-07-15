# DeepLaw Agent Adapters

DeepLaw integrates with Codex, Claude Code, OpenCode, and, in a later change,
Analytix through one local read-only MCP process. The adapter contract is small
on purpose: the server exposes one tool with leaf name `law_support`, and every
operation goes through that tool.

This document describes adapter behavior only. Corpus building, release
governance, and retrieval internals are separate concerns.

## Stable boundary

The common process command is:

```text
deeplaw mcp --stdio
```

The common server key is `deeplaw`. Hosts add different prefixes to MCP tools,
so the visible name can differ, but the server-level leaf name must remain
exactly `law_support`. For example, OpenCode renders it as
`deeplaw_law_support`. Host namespacing does not create a second public tool.

`law_support` routes eight operations:

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
host. From a DeepLaw checkout, use a user-level tool install for normal use:

```bash
uv tool install .
deeplaw --version
deeplaw official install
deeplaw official status
```

Contributors who intentionally want live source edits can instead use
`uv tool install --editable .`; a normal user updates checkout code with
`uv tool install --force .`. A project-only `uv sync` is insufficient when the
Agent launches the plugin from another working directory unless its environment
also exposes that project's `.venv/bin`.

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

The same plugin root also contains `.claude-plugin/plugin.json`. For a local
development session:

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

## Future Analytix connection

Do not modify Analytix merely to make DeepLaw globally visible. Add the bridge
in a later, scoped Analytix change with these invariants:

1. Register an optional DeepLaw stdio MCP profile, disabled for normal tasks.
2. Start `deeplaw mcp --stdio` only after an explicit legal-research action or
   an explicitly selected legal mode. Do not use a free-form classifier as the
   sole activation gate.
3. During MCP initialization, allowlist exactly the `law_support` leaf tool and
   reject any other DeepLaw tool before its schema reaches the model.
4. Add the tool schema only to the legal-research turn. Preserve Analytix's
   stable system prefix and ordinary data tools for every other turn.
5. Keep private case documents, facts, chats, embeddings, SQLite state, and
   DuckDB data inside the case project. Send only a minimal de-identified legal
   issue to the official DeepLaw scope. The optional DeepLaw user-private scope
   is for legal references, not an Analytix case store.
6. Keep the two-stage pattern: bounded `search`, then exact `get` and selective
   `verify`. Do not place broad retrieval results in the main context.
7. Fail closed. If the process, release, receipt, or version check fails, report
   that DeepLaw is unavailable. Do not fall back silently to model memory, web
   search, generated topic-page text, or case-private retrieval.
8. Log only operational metadata needed for diagnostics. Do not log raw legal
   queries when they may encode case facts.

This keeps DeepLaw additive: legal-source research becomes available when
requested without changing Analytix's behavior, context size, or tool choice on
non-legal data work.

## Adapter validation checklist

Before publishing an adapter release:

1. Parse both plugin manifests, `.mcp.json`, `agents/openai.yaml`, and the
   OpenCode sample.
2. Run the Codex plugin and Skill validators.
3. Run `claude plugin validate ./plugins/deeplaw --strict` when Claude Code is
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
