# DeepLaw v0.13 Pass 11 Host token-attribution protocol

Status: **frozen development protocol; not executed**. Package remains `0.12.0`;
`release_ready=false`; no operation profile is admitted by this document.

## Why this protocol exists

The current autonomous `knowledge_support` advertises one read-only tool with 19 operations. Local
canonical serialization measures schema bytes, not model tokens. Historical Codex receipts report
total provider usage but contain no A/B/C/D paired control, profile identity, or advertised-schema
receipt. Therefore they cannot attribute token cost to DeepLaw or prove that the 19-operation input
schema is material.

The source-candidate runtime currently measures:

- 19 exact operation names shared by the Python Literal and output contract;
- full runtime tool input and output schema bytes through deterministic canonical JSON;
- no current `operation_profile` or `enabled_operations` behavior.

These are construction facts only. They do not authorize a runtime profile.

## One-workflow A/B/C/D matrix

All four conditions use the same installed Codex binary, existing ChatGPT login, model
`gpt-5.6-luna`, reasoning `max`, natural-language task, non-secret task binding, structured output,
hardware, provider network policy, and exact candidate wheel. The Host starts a new thread for each
condition within one qualification workflow. The prompt directs the Host to use only Context
operation fields and omit null or unrelated-operation fields; it still contains no expected answer,
marker, exact knowledge identity, Gold, or scorer material.

| ID | Condition | Purpose |
| --- | --- | --- |
| A | Host without MCP or dynamic tools | no-DeepLaw control |
| B | Host with one context-only dynamic tool input schema | isolate the minimum Context schema |
| C | Host with the current full `knowledge_support` dynamic input schema | isolate the 19-operation input-schema delta from B |
| D | Host with exact candidate-wheel `knowledge_support` MCP and the full task | measure the actual read-only DeepLaw task, Provider result, and transport |

Codex App Server experimental `dynamicTools` is a measurement instrument for B/C; it is not a
DeepLaw server or product surface. Both conditions validate the requested Context arguments and
receive the same bounded Provider Capsule produced by the frozen read-only preflight. D is the
actual exact-wheel MCP process and is the only condition used to establish real DeepLaw transport
behavior. This keeps the B/C result constant while varying only the advertised input schema,
avoids a second MCP server/engine, and avoids changing runtime behavior merely to create a control.

The protocol is bound to the locally installed `codex-cli 0.147.0-alpha.1.2` generated App Server
schema: `thread/start`, `turn/start`, `thread/tokenUsage/updated`, and the experimental
`item/tool/call` request/response shape. The runner records the exact binary version/hash; another
version is a different observation.

## Frozen attribution rule

The runner records advertised canonical schema bytes and provider-reported input, cached input,
output, reasoning-output, and total tokens. It samples the aggregate RSS of the App Server process
tree, so D includes the exact-wheel MCP child. Missing usage remains `unreported`, never zero.

- `B − A`: minimum tool-schema marginal tokens;
- `C − A`: full input-schema marginal tokens;
- `C − B`: marginal cost of the operations outside Context;
- `D − C`: actual MCP/task/Provider-result marginal, not a pure schema value.

Schema overhead is significant only when all four conditions report usage and:

1. full advertised schema minus Context-only advertised schema is at least 8,192 bytes; and
2. provider input tokens for C minus B are at least 512 tokens **or** at least 5% of B input tokens.

The thresholds are frozen before a provider run. Negative deltas, missing usage, a failed
condition, schema drift, model rerouting, or uncontrolled tools cannot admit a profile.

If and only if the rule passes, a later minimum candidate may add an operation profile inside the
same `knowledge_support` process. The legacy full operator behavior must remain available, no v6
operation contract may be deleted, and the profile needs synchronized contract/help/adapter/tests.
Until an executed report passes the rule, `profile_change_admitted=false`.

## Candidate/evaluator boundary

Candidate-visible inputs contain the natural task, allowed Host/tool behavior, and non-secret task
binding. They contain no expected action, expected decision, marker, exact `knowledge_id`, Gold, or
scorer. The candidate runner may retain the neutral structured final answer and bounded Provider
Capsule in its external observation, but never Host transcript or reasoning.

Evaluator-only Gold and scorer compute First Correct Action, Decision Preservation,
Wrong-State Admission, Useful Context Recall, RelevantChars/ContextChars, Duty Coverage, Gap
correctness, Duplicate Evidence, Redundancy, and Distractor Answer Delta after execution. Candidate
output cannot mount or import the evaluator tree.

## Security and receipt boundary

The runner uses App Server thread APIs and the Host's existing login. It does not read an auth file
or token. Every condition uses an ephemeral thread. A-C explicitly clear configured MCP servers; D
enables only the generated closed-environment wrapper and verifies its exact child argv. The
observation binds the wheel hash, runtime executable hash, wrapper hash, Codex binary hash, commit,
and tree. Sanitized JSONL retains only method/item types, status, opaque-id hashes,
argument/result hashes and byte counts, usage, and disallowed-tool markers. Account rate-limit and
remote-control notifications are discarded rather than projected. A failed tool call is retained
only as a bounded status/failure-code category, never its raw error or result. Raw stdout/stderr,
agent reasoning, transcript, environment values, credentials, and absolute paths are not
persisted. The output directory is outside the repository and evaluator tree.

Provider result bytes remain bounded by 65,536. This is a hard safety ceiling, not a quality target.
Ledger before/after equality, missing usage, wrong tool, unexpected mutation, path/Secret leak,
payload overflow, Host/model drift, and candidate/evaluator contamination are hard failures.
