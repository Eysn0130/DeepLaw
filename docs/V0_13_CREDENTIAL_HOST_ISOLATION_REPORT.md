# DeepLaw v0.13 credential and host-isolation disposition

Status: **local isolation regression passed; real model calls blocked / not executed**
(2026-08-08). This is source-candidate evidence, not a real-host quality result.

## P0 reproduction and remediation

All three execution paths previously inherited `os.environ.copy()`: the Living Wiki host path and
the single-phase and phased Semantic host paths. A regression canary proved that this operation
made `DEEPLAW_TEST_AMBIENT_SECRET` visible to the host. Because an ordinary host passes its
environment to MCP children, putting a Provider Secret in that same environment would also expose
it to DeepLaw MCP.

The harnesses now construct a closed allowlist containing only platform/runtime necessities
(`PATH`, locale, platform process variables and temporary-directory variables) plus explicit
DeepLaw fixture inputs. Each invocation creates a private mode-0700 isolation root and replaces
`HOME`, `USERPROFILE`, every XDG directory, temporary directories, `CODEX_HOME` and the OpenCode
configuration directory. The host working directory is the isolation root, not the repository.
The entire tree is removed after the host and its MCP child exit.

There is deliberately no generic environment-secret opt-in. Provider authentication must use a
host-specific channel proven not to reach MCP descendants before this harness can execute a real
provider. This fail-closed boundary is stricter than accepting a key and attempting to redact it
after process launch.

## Canary evidence

The focused command was:

```bash
uv run --frozen pytest -q \
  tests/test_v013_host_environment_isolation.py \
  tests/test_living_wiki_delivery.py \
  tests/test_semantic_gold.py
```

Result: passed. The fake host starts a fake MCP child using normal inherited-process semantics and
proves all of the following:

- ambient and Provider canaries are absent from Host and MCP environments;
- canaries are absent from argv, prompt, stdout, stderr, structured report and written artifact;
- required `PATH`, locale and temporary-directory variables remain usable;
- Host and MCP use the isolated HOME/XDG/Codex/OpenCode paths and isolated working directory;
- the isolation tree no longer exists after completion;
- all three real-host harness paths use the same closed environment builder.

Current file bindings:

| File | SHA-256 |
| --- | --- |
| `benchmarks/hosts/run_living_wiki_host_harness.py` | `582c9bf3a0e7cd50aa9106b4e2ef5c1ed70895084931caf26154d538b1a467b3` |
| `benchmarks/hosts/run_semantic_host_harness.py` | `812b24547c7562f7f5c57de602bb07dd5f9f2680e623942863ecc691b975ce21` |
| `tests/test_v013_host_environment_isolation.py` | `98746d578fb91dc2c91cdb712d9f351430b74bc001916a210169974eba8b6ff2` |

## Credential gate

No previously exposed DeepSeek credential was used, copied, printed, persisted, searched for or
placed into any process. The owner-side revocation of that credential cannot be observed from this
repository. No new repository-external, owner-only evaluation credential path was provided.
Therefore every DeepSeek/OpenCode network call remains blocked.

The current desktop/session authentication state is not an admissible Codex evaluation key and was
not copied into an isolated HOME. A Codex CLI executable is locally present, but the required
evaluation-only project/key, repository-external blind corpus and Human Gold are absent. OpenCode is
not installed in the qualification environment. Consequently no real Codex or OpenCode/DeepSeek
model task or preflight was executed.

Current upstream requirements used for the pending Track B design remain the official DeepSeek
API endpoint/model documentation and OpenCode provider/configuration documentation. Configuration
merge behavior means a future run must still capture resolved configuration and prove that no
global provider, tool, MCP or sharing state was inherited.

## Disposition

`secret_exposure_count=0` for the executed deterministic canary suite.
`real_codex_three_runs=not_executed` and
`opencode_deepseek_three_runs=not_executed`. The missing owner credential, Human Gold and blind
inputs are hard prerequisites, not optional follow-up evidence. No RC, GA or real-host quality claim
is authorized.
