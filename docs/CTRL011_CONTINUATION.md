# CTRL011 engineering checkpoint and continuation

Status: active engineering continuation as of 2026-10-02, not a qualified release.
`release_ready=false`. The owner resumed end-to-end v0.13 construction and authorized
the existing release workflow once all current mandatory gates pass. The checkpoint
history below does not authorize treating incomplete producers as formal evidence.

## Current continuation record

### 2026-10-03 complete-check result and manifest pin repair

The stable engineering candidate `e368b3506cd00f0d8ae1d0cb98b4970dca0166ab`
(tree `b52f63ebf7ebbde13d260baa1c21339d0a9f3a55`) ran one complete
`uv run pytest`: 4,450 passed, three failed and 11 skipped in 1,261.39 seconds.
The test inventory was exactly 4,464 nodes. JUnit SHA-256:
`36851801824f6d5756597d4910b7530941f7a4ffb70156210542a3f74de75bab`.
Its five Fast PR jobs in run `37104288037` passed, including Windows and the
three macOS interpreter cells; that result binds this commit only.

Two failures were caused by the implementation omitting the typed consumer's
frozen source-hash update after reconciling the inventory. The tracked v2
manifest contains exactly two new common cases and five qualification cases;
no old node or classification was removed. The consumer now pins its actual
SHA-256 `b2ec782a15e55ca7325d74a86098c5be5c5db0d4be66e242eca666badec6a095`.
The two failed public receipt nodes and the related manifest positive/negative
checks passed together (14 cases), without changing assertions or accepting
arbitrary caller manifests. This targeted repair does not turn the preceding
complete-check result into a pass on the newer candidate.

The third failure is the original real OpenCode loader node: the native
`session.created` event was observed, but the Python entry marker was absent.
The production three-second deadline and original assertions are unchanged.
The attempted passive collector misread `proc_listchildpids`' PID count as a
byte count and retained no target observation; that capture is invalid and
cannot support a root-cause or pass claim. The original test verdict is intact.
The loader cause remains unresolved. No final qualification, package, merge,
tag, signing, release or post-release installation follows from these checks.

### 2026-10-02 strict runner and topology declaration implementation

The new sole writer consumed the predecessor's explicit release and preserved
the three documentation candidates above checkpoint `de5c0e6`. The primary
checkout's six protected files remain outside this work package.

The typed Host-task consumer now dispatches the existing separately versioned
owner-guard declaration without projecting its key-free Host or bounded public
inspection into the old Secret-bearing-parent contract. This is a current
engineering fix, not formal topology admission. Every owner-guard declaration
still derives a process-observation hard failure and `isolation_observed=false`;
matching self-reported digests cannot pass a Core gate. Private-store reads,
key delivery and raw retention are rejected by the closed source schema. The
old source retains its original rules. Two public parser regressions first
failed at the unsupported source boundary, then the affected Host-task and
owner-guard regressions passed after the fix. An independent read-only review
found no admission bypass.

The explicit strict macOS launcher is a prerequisite for trusted-owner
deployment. Authority launch and native Host control must stay with the trusted
owner; the production runner/scorer FD connection has not been implemented.
Its result therefore retains `production_runner_integrated=false` and no formal
qualification. See `benchmarks/hosts/NATIVE_SLOT_ENGINEERING.md` for the actual
policy and synthetic challenge boundary.

The runner inventory was refreshed: zero registered qualification runners,
with repository administration available. The current workflow requires the
actual external Host producers and retained exact-candidate workflow artifacts.
There is no existing signed-owner-evidence import route. Registering a new
runner needs a new registration credential, outside the current prohibition
on obtaining new Secrets. The labels alone cannot replace isolation evidence.

The loader's bounded unsampled libproc check passed and confirmed the exact
Python image, only three child descriptors, no traced/resource-suspended state,
and a short pre-user-code wait. This rules out the observed run exhausting
Darwin's spawn descriptor bound; it does not explain the retained three-second
failure. The original timeout and production deadline remain unchanged.
No new Provider request or credential operation was performed. Final Core,
Host/C6, packaging and release remain unexecuted; this record is not a release.

### 2026-10-02 contract and loader boundary review

The current takeover verified clean checkpoint
`de5c0e660381dbd7e2193b85e3feccdd2d257f82`, tree
`da42bca19e38f0996a23d7d703e6eda83c683550`, and the unchanged lock digest
`a8e33e7d390bb7f94528c75827176b46b85f7539d27c51923700f38ccaf732d7`.
The predecessor explicitly released write ownership. No final artifact candidate
or new qualification evidence was produced by this review.

| Required boundary | Actual consumer | Current source and gap |
| --- | --- | --- |
| Exact Host/MCP isolation, Secret non-delivery and negative canaries | `typed_qualification_evidence_v3_host_tasks._isolation` and process receipt v2 | Guest UID/namespace/mount/seccomp/cgroup observations are engineering evidence; existing negatives cover runtime writes and non-loopback connections, not complete Secret non-delivery/access |
| Frozen formal credential topology and private-data exclusions | Task-result/v3 evaluation and `v013-host-task-evidence/v1` | The old source requires a Secret-bearing Host parent and all read flags false; the external-authority producer honestly reports a key-free parent and native-message inspection, so a versioned semantic migration is required |
| Actual qualification workflow and artifact provenance | Kernel, Commercial and release workflows | Kernel requires `[self-hosted, macOS, deeplaw-kernel-qualification]`; the repository runner API returned zero registered runners. Local run IDs cannot replace the successful GitHub run and retained artifacts |
| Six actor process/source/instance identities | Candidate `host-owner-guard-isolation/v1` only | This source is not enabled in the formal consumer and its validator always returns `formal_admission=false`; it is not an additional original Core gate |

The earlier checklist's independent six-role authority identity step describes
that candidate design, not a requirement established by the original Core
consumer. Removing that assumption does not supply the missing safety evidence.
The current authority uses ordinary same-UID Popen and owner-only files: this
supports non-delivery through its closed IPC/environment, not a claim that the
runner cannot read the credential. Formal acceptance of an external authority
must preserve the real Host/MCP, credential, private-store, retention, canary and
exact-candidate boundaries; changing booleans or enabling a declaration validator
alone is insufficient. No actual key was moved or new credential obtained.

The installed loader Host embeds Bun `1.3.14+0d9b296af`; global Bun `1.3.11`
is a different runtime. One unchanged focused loader test passed. Copied
diagnostics preserved the production three-second deadline and original
session/argv/environment/interpreter assertions. Sampled shebang and fixed-env
starts timed out before Python's first statement, while direct invocation of
the same interpreter and script completed. A later sampled shebang start also
completed, and its actual interpreter image matched the direct control. Sampling
can perturb startup; the initial dyld stack is not a proven deadlock or root cause.
Matching Bun source does not set `START_SUSPENDED` on this normal spawn path.
No production workaround, deadline expansion or test replacement was made.
The original unsampled timeout and retained full-suite failure remain unresolved.

### Earlier bounded correction record

The next sole writer resumed from clean `85af7d58d2f10fd39f2306d5a0ee5f0995889993`
after the predecessor explicitly released dd2a. The exact commit's Fast PR
`37053774604` is completed/success. PR #42 remains draft/open and the latest
formal release remains v0.12.0. These facts do not qualify v0.13.

The original fixed probe's safe native response metadata is `finish=length`,
224 input, 248 reasoning and 8 output tokens. The engineering Host output bound
is now 1,024 tokens, with the owner entry enforcing the same single-request bound;
formal task budgets and acceptance models are unchanged. One new actual request
in `successor-budget2` completed with `deepseek-flash`, `finish=stop`, an exact
fixed-reply match, 224 input, 98 reasoning and 10 output tokens. Host/MCP exited
0 with empty cgroups, the authority exited 0 and confirmed cleanup, and the VM
exited 0. Observation SHA-256 is
`310752deb3b19773b99b821666522f9fd5d2b35fcebe499a1637c98c76de5baa`;
Provider observation SHA-256 is
`fba74510a387d7f20c644a35d94c5b3760c14b707e51e17e4f533dc2f237d9de`.
The initrd SHA-256 is
`5b9f3f1e084519975cbbd55fd63585f55e6e09b00efefde405c2794c796ba404`.
This retains the c5b55d7 wheel and separately binds newer guest module bytes:
`mcp_exercised=false` and `formal_admission=false`. The original truncation and
an earlier context-label binding failure are retained. The latter happened
before authority startup, credential loading or outbound dispatch; it is not
a failed Provider request.

A temporary loader stage probe reproduced the unresolved failure without a full
suite: resolver entry, spawn request and spawn return were observed, followed by
`continuity_resolve_timeout` and a Bun child exit code of 143 before the Python
fake's first statement. This narrows the failure beyond the native session
event, but does not establish the child's OS exec/start boundary or its cause.
Focused loader and adjacent regressions pass; that does not erase the retained
complete-suite failure. The production 3-second deadline and argv, environment,
session and interpreter assertions are unchanged. The installed macOS loader
Host is 1.18.16, executable SHA-256
`a41776bf64c75786d6baf531b840ffb873c090d7c44793ae2dd4b1896de56a1f`;
it is distinct from the frozen Linux 1.18.16-deeplaw.3 Host.

Independent observation feasibility has been checked against the actual contract,
not an assumed ES requirement. `host-owner-guard-isolation/v1` requires six
role-bound process/source/instance identities, credential delivery exclusions,
private-store/retention observations and native/execution/challenge bindings.
It does not mandate macOS EndpointSecurity. The current authority still declares
`local_popen_pid_nonce_binding`; the Linux procfs/cn_proc producer observes only
the guest Host/MCP and cannot observe the macOS authority. Moving the authority
into the existing no-NIC guest has no installed owner-only credential-delivery
or bounded outbound path that excludes the runner/observer. No key was injected
into the guest and no network topology was widened.

The bounded macOS checks found zero registered system extensions and EACCES when
opening auditpipe and DTrace without reading any events. Current SDK/XNU headers
state that kqueue descendant tracking is unsupported; process notifications alone
do not supply the required complete descendants, store-read or credential-delivery
observations. The existing ES probe is ad-hoc signed without the ES client
entitlement and is only a zero-event capability probe. Thus sudo alone is not a
sufficient remedy. The remaining facility is an authorized independent collector
for the actual credential-authority OS, or an owner-controlled Linux authority
deployment with Secret excluded from runner/observer and a bounded observed
egress path. That collector must produce the role fields for the existing
validator and subsequently enabled role-bound consumer; installation or mere
permission would not itself pass the gate.

The 207 directly affected probe/authority/builder regressions and 17 loader/
adjacent regressions passed; lock, Ruff and patch checks passed. A new full suite
and formal qualification were not dispatched while the loader cause and native
authority prerequisite remain unresolved. All 13 Core gates, six formal Host
slots and 15 real C6 tasks remain `not_executed` for a final candidate. Final
packages, bundle reopen, merge/tag/signing/release and post-release installation
remain unexecuted; `release_ready=false`.

### Earlier successor record

The successor follow-up resumed from clean `533682dec03dcf5a2475da9c078f954c3b8af82c`
on 2026-10-02. Current v3/v9 qualification inputs now explicitly pin OpenCode
`deepseek/deepseek-flash` and response model `deepseek-flash`; Codex remains
`gpt-5.6-luna`/`max`. Historical v2 schemas and retained evidence keep their original
model identity and hashes. No gate status has been promoted.
The existing inventory generator now freezes 4,426 common and 4,431 Windows
cases, original-file SHA-256
`17d8df53769546f2411f54752c5eefaf3ac4e2616d7873bdde4386cc2579f7a8`
and intrinsic digest
`d57a43ff38abd7d4461f433d230698b3c1109924949ac611ffcc1ec9e2b5beaa`.
Its complete, non-overlapping inventory checks passed locally.

One bounded engineering Provider request independently reported `deepseek-flash`
in the response stream. Its response SHA-256 is
`3a486b57d48a8fc165a2f12251a14582a2bb70dce6025c0075d9267e311225ad`.
The 256-token limit truncated the fixed reply, so that score failed; no retry or
budget expansion followed. The authority child alone loaded the existing key,
reported it unchanged, completed one outbound request, and confirmed cleanup.
The engineering run used retained checkpoint packages and is not final-candidate
or functional-MCP qualification evidence.

The existing proxy now binds OpenCode's two session headers to the observed native
session. A separate no-forward native regression after the TCP reset repair
observed four control requests, one auxiliary flow, zero kernel drops and clean
VM/authority exits. The original observation SHA-256 is
`a5a7f6690a9129b892b4abc4b29d65d05d856a24518707292ad5f9b090d7ea82`.
Its expected terminal gap is `model_probe_not_observed`; no credential was loaded
and no outbound request was attempted.

The loader fixture no longer adds a standalone Python preflight to the native
loader contract and uses a stdlib-only interpreter invocation. The production
3-second CLI deadline and original 5/15-second test bounds remain intact. A final
finite diagnostic measured the complete spawn/output/exit chain at 136 ms, but
neither it nor other focused passes explains the earlier intermittent missing
resolver. That failure remains unresolved.

The successor complete check finished with 4,445 passed, one failed and 11
skipped in 1,251.58 seconds. The sole failure is
`test_exact_opencode_loads_project_plugin_and_dispatches_native_session_event`:
the actual `session.created` was observed, but the resolver did not enter.
Disabling Python site hooks therefore did not close the failure. The original
JUnit SHA-256 is
`8caf49794b06a9c8473a27ae6ad3281430c5fe84da84fc78c64f86c8eda979e6`;
the frozen check-input record SHA-256 is
`eba91a1da2097f784cab7d0f8b6e91870d85cbe31ca48d734dd37a72732da968`.
All recorded source hashes stayed unchanged during that check. Focused current
and historical contract checks passed, as did `uv lock --check`,
`uv run ruff check .` and `git diff --check`. A bounded read-only investigation
found no preceding test that left a parent resource limit, signal handler or
environment mutation unrestored; it does not exclude transient resource pressure.
The failed complete check remains a delivery blocker, separate from native
qualification authority. It was not rerun as a diagnostic.

Formal owner-process observation is blocked by a measured external prerequisite:
the actual EndpointSecurity capability probe reports `endpoint_security_root_required`,
and `sudo -n` reports that authentication is required. No root credential was
requested or read. Entitlement and system authorization are still unobserved;
root alone is not asserted sufficient. The repository has no registered self-hosted
qualification runner. Six formal Host slots, 15 real C6 tasks, final bundles and
release remain unexecuted. `formal_admission=false` and `release_ready=false`.

The checkpoint observations below predate this follow-up.

P0 OBSERVED on 2026-10-02: dd2a was clean at
`c5b55d7c2149e81ad480cc2bbbd4dc2efd607cc8`, tree
`309544e4aebc5d6a1f64196972680bd2ed1534d0`. The remote PR #42 branch has the
same HEAD; main remains `5990687918e7b017e40da00a2c258267bc41e7f5`. PR #42 is
OPEN/UNSTABLE, Fast PR `35120075249` completed FAILURE, and the latest release is
`v0.12.0`. The predecessor chat is idle and reports handing over dd2a write ownership.
No running DeepLaw test, Host, native guest, or build process was found in the bounded
process inventory. Other checkouts and their changes remain protected.

The three named temporary native/build resource directories are absent. Reconstruct
only required inputs from recorded source/version/rights identities before executing
a native slot. Historical runtime observations remain development evidence.

| User outcome | Public entry | Existing decisive evidence | Remaining gap | Next action |
| --- | --- | --- | --- | --- |
| A: install, configure, upgrade and recover | CLI and exact-wheel journey | Existing CLI/migration regressions; old Candidate Full journey belongs to f5d7b54 | Final artifact installation and Windows environment retest | Reuse current journey after final freeze |
| B: preserve source-native evidence and bounded drill-down | ingest, source read, support read | Source/locator/OCR regressions and PRD source mapping | Final generic professional-source Core evidence | Bind existing task evidence to final artifact |
| C: one governed mutation kernel | owner grants, sink, Coordinator | Grant/idempotency/conflict/recovery regressions | Final canonical-integrity and recovery evidence | Use current Core collectors after stabilization |
| D: readable, rebuildable Living Wiki | project, reconcile, typed read | Projection/link/user-file regressions | Final profile/rebuild/scale evidence | Use current Wiki and scale tasks |
| E: admitted bounded context | query, context, explain, read | Admission/budget/disclosure regressions | Actual Provider delivery observation | Wire native producer to broker and collector |
| F: real task continuity | thin Codex/OpenCode adapters | Native lifecycle engineering and local continuity regressions | Six final Host slots; functional Host-to-MCP and fork binding | Close one zero-model chain and one minimal real slot first |
| G: bounded C1-C6 behavior | shared kernel and maintenance harness | C1-C5 regressions and synthetic C6 evaluator | Frozen three-configuration/five-task real C6 results | Reuse the existing Host execution chain |
| H: exact supported release | Candidate Full, bundles, release tooling | Old six POSIX cells and wheel/10k evidence, tied to old inputs | Current Platform Core drift; final nine cells, 13 Core, bundles and post-release install | Reconcile the active inventory before final qualification |

Implemented remains distinct from Verified, Qualified and Released. Current finite
blockers are incomplete native producer/broker/collector deployment,
unexecuted real C6 tasks, and final-candidate qualification/release inputs. Optional
Capability and Competitive/Research evidence remain non-gating under classification v9.
P1 inventory reconciliation and its focused regressions passed locally; new tests
must be included in the next manifest freeze. Actual supported-platform CI remains
pending. No real model execution had been observed at that checkpoint.
The first full-suite attempt was stopped after
`test_platform_gate_accepts_no_skip_suite_and_rejects_a_skip` exposed a changed
legacy error message. That entry still rejected skips; the smallest repair
restores its original zero-failures/errors/skips wording while retaining the
manifest-aware rejection rules. The original failure remains distinct from the
required rerun.

The second full-suite run completed with 4,367 passed, 7 failed and 11 skipped
in 1,235.58 seconds. Its failures remain recorded separately from repairs:
the platform source pin used the manifest's intrinsic digest instead of its
original-file digest; a 4 MiB pytest parameter expanded into a 4,194,402-character
node ID and made the frozen manifest exceed source-deployment and workspace
bounds; a new schema was absent from the tracked build inventory; two current
contract/count assertions were stale; and the isolated real OpenCode loader test
did not observe its resolver. Short, explicit parameter IDs preserve the original
boundary input and assertions. The first repaired manifest was 1,624,383 bytes with
original-file SHA-256
`7436baa4097d9282215bb78c902ec98b677058917bc7038a537be2569e7650e0`,
distinct from its intrinsic digest
`56d32bbf1e3b2efa70251ea8d1c3dabb8cb68c82acd4ea7555c34abfb5ce6a89`.
The corrected source deployment, tracked-schema build, platform bindings,
runtime-version assertion and bounded repository snapshot passed focused checks.
That platform receipt checked the exact OS whitelist: Linux 15, Darwin 2
and Windows 244 nonapplicable inventory rows. No provider or workspace limit
was increased. After the isolated authority, attachment stager and binding-hook
regressions were included, the current freeze contains 4,418 common and 4,423
Windows cases. Its original bytes are 1,664,121 bytes, SHA-256
`7ccc227a18700bb30a44343be78abb695d7ad34051b8122c938b10c09723ecbe`,
with intrinsic digest
`4f5def16fea254a1655889b89a0575b8bf38d804d20feaf14cb045d0f6855656`.
The exact current OS whitelist is Linux 15, Darwin 2 and Windows 285. This is
inventory reconciliation, not an actual supported-platform execution receipt.

The loader-only diagnostic observed the actual `session.created` before the
missing resolver, so the original failure does not establish a plugin-loading
failure. Two isolated diagnostic runs and an unchanged original-test rerun
subsequently passed; the first resolver failure remains unclassified. The test
now disables unrelated models.dev acquisition, binds its public native session
event and resolver arguments, and distinguishes a missing event from a missing
resolver. Its original 5/15-second observation/readiness bounds remain intact.
This focused pass is not a complete-suite pass or a formal Host receipt.

The third full-suite run then passed: 4,437 passed and 11 skipped in 1,228.62
seconds. Its stable candidate diff SHA-256 was
`e71ceb23dc9c96858e567058caa526c798a22104c04d04846c81b7503c59bd15`.
This closes the seven second-run failures locally; it is not supported-platform
CI or release qualification. A subsequent independent review found that a supplied
model callback falsely implied `credentials_supplied=true` even in a dry probe.
That field now remains unknown for model callbacks and false for the zero-model
entry; existing callback/zero-model regressions assert those meanings. Required
validation must bind the resulting final engineering candidate before delivery.

The fourth full-suite run completed with 4,436 passed, one failed and 11 skipped
in 1,243.99 seconds, against stable diff SHA-256
`90a3dfeeface03aebfbc14e03751f7ef1a51053118ecf9c6948fec14922afdf6`.
Only the installed OpenCode loader's local resolver was missing; the actual
session event was observed. A temporary copied-plugin diagnostic then reproduced
the failure after event recording, valid workspace/vault checks and successful
spawn: the three-second child deadline elapsed before the fake resolver's first
Python statement. The fixture's PATH selected the macOS developer-tool Python
shim. Changing only its shebang to the test interpreter made the paired diagnostic
exit zero and return the expected public gap. The public fixture now binds and
asserts that interpreter. Adapter behavior and the 3/5/15-second bounds are unchanged.
The original failure and diagnostic metadata are preserved separately; the
resulting candidate still requires a fresh complete check.

The fifth complete run preserved that failure: 4,437 passed, one failed and
11 skipped in 1,285.54 seconds, with stable diff SHA-256
`e0f494b710c4cca0968dd4e3b4d2474fd737a0fe57676fd8793e00220caceb9e`.
The fake's exact test-interpreter shebang was present, but its observation was
missing after the native event. Thus interpreter binding alone did not resolve
the intermittent failure. A subsequent copied-plugin pair observed the expected
PATH and selected fake executable with both relative and absolute command paths;
it did not establish a PATH fault. The fixture now checks the fake CLI before
starting Host, with the same closed environment and three-second deadline, and
keeps that check from writing either Host oracle file. The actual call has a
first-statement entry marker. Auto-update, default plugins and Claude scans are
disabled, matching the native closed profile; DeepLaw's project plugin remains
enabled. All session, argv, environment and deadline assertions remain. The
focused pass and preflight do not prove the original intermittent root cause or
cold-start acceptance. Fresh complete validation remains required.

The sixth complete run finished with 4,437 passed, one failed and 11 skipped
in 1,268.64 seconds against stable diff SHA-256
`276d0f33d599cb07c1ad247d0145f67f5e526e5da59faea0915635ce9fc3a7ba`.
Its JUnit SHA-256 is
`2dc889df6f68a0e80a83fe1b51f3da463d077db71414bb3302b51bfb290766f0`.
The sole failure was the direct fake-CLI preflight's three-second timeout,
before OpenCode startup. Preflight intentionally writes neither entry nor
observation, so their absence cannot locate the timeout inside Python. A bounded
follow-up using the same script, cwd, four environment entries and deadline
returned zero; a temporary staged-import script also returned zero via both
shebang and explicit interpreter, each in less than 0.14 seconds. Its observation
SHA-256 is
`99f0f245ae0b1173fbd3abdda4030d29e0065814a7968eac927e769bb71c92d3`.
This did not reproduce or explain the failure and does not establish that prior
resolver failures share its cause. No further full-suite retry or deadline
expansion was made. This engineering checkpoint retains the failing check.

Only descriptive build metadata and these evidence notes changed after that
complete run; the executed source and test candidate remains the one above.
The new `.3` engineering archive binds the exact executable and its generator
configuration. Static package admission passed, but no formal Host identity,
model-task completion or release qualification follows from that result.

The missing native resources have been reconstructed from public frozen inputs.
The distinct OpenCode `1.18.16-deeplaw.3` build and Linux runtime inputs have new
records under `benchmarks/hosts/upstream/`. An offline VM compiled and imported
the three source wheels successfully. Current engineering guest assembly uses
an exact c5b55d7 checkpoint wheel plus separately hashed current guest modules;
this is explicitly not final-candidate qualification. The corrected zero-model
fork-only run passed with bound actual Host/Python executable observations,
closed role processes, empty cgroups, normal VM exit and zero route drops. A
second zero-model run connected the actual Host to MCP and observed health,
session creation, MCP status and fork routes, with four closed connections,
normal VM exit and no owner failure. Its original observation SHA-256 is
`4eb894d1a6d0a3a4ef41254197235ae4414bd00bcf41c7afa0501da839745d3d`;
`mcp_exercised=false` and model invocations remain zero. This establishes
connectivity only, not functional domain-tool use. A separate, explicitly
enabled one-request model probe remains engineering-only.
Its initial dry runs exposed a frozen Host config limit requirement and a
4100 auxiliary-flow observation gap, now covered by focused regressions. No
real Provider request has been sent. The current public snapshot marks the
original `deepseek-v4-flash` model deprecated, and the exact upstream Host
removes deprecated models. The resulting model-not-found diagnosis is consistent
with the [DeepSeek change log](https://api-docs.deepseek.com/updates/): V4 Flash
was retired on 2026-09-10, and its legacy name now routes to V4.1 Flash. A
successor frozen model identity requires an explicit acceptance-contract change;
the historical pin and evidence remain unchanged while that choice is pending.

The sealed dry probe also exposed an unrelated concrete network defect: Python's
HTTPServer binding performs reverse DNS even for the proxy's literal loopback
address. The private packet diagnostic observed loopback UDP to port 53; a
controlled bind independently reproduced the resolver call. The minimal candidate
binds through TCPServer and sets the same HTTP server metadata without that lookup.
A regression fails against the original HTTPServer and passes against the fix.
The private native candidate then observed all four closed control routes with
zero kernel drops and no auxiliary Provider flow. Host/MCP roots exited zero,
their cgroups were empty, the authority group was confirmed empty and the VM
exited zero. Its observation SHA-256 is
`ee69550287336988305d719aa62a476fb61eebe194a89cc66176f20f6c1ba5ec`.
Overall status remained failed at `model_probe_not_observed`; credentials were
not loaded, outbound attempts were zero and invocation count remained unknown.
This is a private engineering candidate bound to the old checkpoint wheel, not
a final public source or formal qualification receipt.

C6 public task corrections, conservative model-execution observation and fixed
15-slot failure preservation have focused regressions. The context fixture now
revokes setup grants before capture, validates the original Capsule before
creating a short-lived outcome grant, and revokes that grant after success or
failure. Credential-free snapshots preserve real Ledger/audit history and strict
offline verification. The read-only context consumer now reopens the original
context, snapshot manifest and Vault closure, validates public task and owner
bindings and independently verifies the original Capsule without reselection or
mutation. The runner binds original trace, provider Capsule and native-event
file hashes. The full-run consumer now reopens all 15 fixed slots, revalidates
their original snapshots and traces, independently recomputes scores and returns
the exact consumed file closure. Complete failed or unknown task scores remain
visible; missing or invalid evidence is rejected. Producer declarations cannot
supply native authority. Actual native attachment admission and the real C6
tasks remain pending.

## Repository baseline

- Repository: `Eysn0130/DeepLaw`.
- Continue from the latest verified head of `codex/v013-kernel-release-candidate-v5`,
  PR #42, rather than silently starting from `main`.
- Prior source candidate: `f5d7b54ef56481998ee918e0739d2041c788686e`,
  tree `3f79f50380cec5017498c079ed1aea5175a02f63`.
- On 2026-09-16, remote `main` was `5990687918e7b017e40da00a2c258267bc41e7f5`;
  latest formal GitHub release was `v0.12.0`. Refresh these facts before continuing.
- Candidate Full run `34321430000` belongs to f5d7b54 and completed FAILURE.
  Its exact-wheel journey/10k and six POSIX cells passed; Windows calibration
  shard 1 failed, shards 2/3 passed, Windows full matrix and aggregation skipped.
  The first failure was the external-install test's closed Windows environment
  omitting SYSTEMROOT (WinError 10106). This checkpoint fixes that environment;
  it does not claim a successful Windows rerun. Do not replay the old run.

## Included work and evidence ceiling

The six governed capabilities were implemented in the prior source candidate:
source/revision dependency support sets, explicit evidence-duty gaps, bounded
external-action state, admitted selection explanations, selective withdrawal and
recovery, and a synthetic maintenance evaluation. These do not establish general
legal completeness, autonomous semantic dependency discovery, external action
success from Host reports alone, or superiority over named alternatives.

This checkpoint adds native isolation engineering, process/audit/loopback
observers, deterministic guest assembly, original fork-byte correlation, bounded
OpenCode lifecycle source patches and build identities, and a fixed real-task
maintenance harness. Read `benchmarks/hosts/NATIVE_SLOT_ENGINEERING.md` and
`benchmarks/hosts/upstream/README.md` for scope and exact engineering identities.
No credentials, native binary packages, private Host payloads or machine-specific
runtime evidence are included in Git.

Retained local engineering observations, not reproducible merely by cloning:

- Linux ARM64 guest under macOS Virtualization: no NIC, shared directory or disk;
  separate Host/MCP UID, namespaces, roots, runtime bindings and cgroups.
- Four entry-phase actual negative probes: both roles denied runtime-entry write
  with EACCES and non-loopback connect with ENETUNREACH; audit loss/backlog zero.
  This is not a complete runtime filesystem/Secret trace.
- `three-route-1`: only health/session/fork, 68 packets, zero kernel drops,
  three connections, network namespace binding, closed role process windows,
  Host/MCP exit 0, empty cgroups and normal VM stop.
- MCP in that run received root-owned EOF without an MCP request:
  `mcp_exercised=false`, not Host-to-MCP functional acceptance.
- Earlier duplicate-health and timeout attempts remain failures, not discarded.
  Waiting for the actual ready line resolved the observed later sequence; the
  causal link to upstream listen-before-handler ordering remains INFERRED.
- These runs used the prior f5d7b54 wheel plus changing engineering modules.
  They cannot be relabelled as final-candidate or formal-slot evidence.

The latest `GuestSlotControl.adapt_fork_source` consumes original private bytes
through the existing native adapter and clears references. It has local tests,
including rejection of a killed Host and byte mismatch. It is not wired to a
complete installed broker/context in bootstrap and has not had a new native run.
Its barrier is the broker's control reply after the child event, not a claim that
upstream HTTP itself delayed its response. No final formal admission is implied.
`linux_host_execution_observer.py` is now present with portable regressions. Its
live snapshots bind executable bytes, role UID/namespaces and the launcher's
process-start commitment to the owner challenge. These engineering snapshots
retain `formal_admission=false` and cannot establish native qualification alone.

## Required next work, in dependency order

1. Preserve the reconciled Platform Core inventory and existing classifications;
   re-freeze only if later test changes alter that inventory. The original drift
   regression has been repaired. Finish one executable
   owner-external broker/collector and actual deployment.
   Reuse existing control-v2, host-process-receipt-v2, native adapter and identity
   freezing tools. Bind candidate commit/tree/lock/wheel/sdist, run identifiers,
   actual Host executable/package, installed broker source/instance, and a fresh
   challenge. Observe actual executed bytes and process identity, not just an
   expected path or configured digest. Close real OS/process/filesystem/network/
   credential boundaries and bounded negative cases. Preserve unknown counters;
   loopback HTTP capture cannot prove all model/Provider/plugin counts are zero.
   The first unresolved identity observation is the owner-side credential authority:
   its source, instance and actual process start must bind the same execution
   challenge and candidate as Host/MCP. Popen PID/nonce commitments and collector
   file hashes alone cannot supply this native Authority. Preserve the old
   Secret-bearing-parent source; enable the role-bound successor only after an
   installed independent collector reopens actual observations and normal terminal
   receipts. A fork probe alone does not complete resume, compaction, wrong-state
   and forgetting coverage.
   Provider forwarding over the isolated boundary and full functional MCP remain
   incomplete. First obtain one admissible slot, then reuse for the remaining
   slots; do not spend six model runs on a producer that cannot be admitted.
2. Complete C6 with the frozen three configurations and at least five actual
   later Host tasks each: source update, wrong experience, independent support,
   unknown external action, and reuse after forgetting. Reuse maintenance files;
   equal budgets/order, public synthetic input, no answer oracle in Host context,
   independent state/event/hash scoring. The 15 real tasks have NOT been run.
   Report costs and unknowns honestly, including no improvement; no cherry-picking.
3. Stabilize behavior, run repository-required checks, then freeze the new exact
   candidate and packages. Only then dispatch one necessary new Candidate Full.
   Never combine old package/tree results into new-candidate success.
4. Complete the same final candidate's nine platform cells, exact-wheel journey
   and 10k, 13 Core gates and six real Codex/OpenCode Host slots; produce and
   offline-reopen Kernel/Commercial qualification bundles. Keep fixed acceptance
   model identities: Codex `gpt-5.6-luna`/`max`, OpenCode
   `deepseek/deepseek-flash` after the explicit successor freeze above. Construction
   model is a separate choice.
5. Once all actual release gates pass, review/merge PR #42, handle any merge-tree
   identity change, use existing authorized signing/release tooling and immutable
   tags, publish verified artifacts, and verify clean post-release installation.
   Do not overwrite tags, fabricate keys, purchase resources, or publish a
   source-only archive as a verified wheel.

## Execution rules for the next ChatGPT task

Connect to the repository with a writable coding environment. A read-only GitHub
connector can inspect and plan but cannot itself execute tests, edit/push code,
access the owner's local VM or perform signing. State that concrete limitation
if encountered; do not claim local artifacts are accessible from a cloud clone.
Complete independent code work and identify exact missing facilities/permissions
instead of asking generally for a broker path. Never upload owner-private runtime
inputs or retrieve credentials into conversation or logs.

Read AGENTS.md, current architecture and applicable contracts first. Preserve
unrelated worktrees, dirty files, stashes and retained evidence. Default to
end-to-end work by one principal implementer; delegate only bounded independent
work with explicit file ownership and review. Do not create another controller
or periodic automation. Keep OBSERVED, THREAD-REPORTED, INFERRED, executed,
failed and not_executed distinct. Stop when finite acceptance is met, or report
an exact non-solvable prerequisite with completed independent work and evidence.

## Historical checkpoint validation (2026-09-16)

Executed against this checkpoint's source on 2026-09-16:

- `uv lock --check`: passed.
- `uv run ruff check .`: passed.
- `git diff --cached --check`: passed after adding an exact-file whitespace
  attribute for the frozen upstream patch's legitimate empty context lines;
  original patch bytes and recorded SHA are unchanged.
- `uv run pytest -q`: FAILED, exit 1, two failures:
  `tests/test_v013_pass21_release_closure.py::test_platform_core_v2_is_complete_non_overlapping_and_active`
  and
  `tests/test_v013_platform_core_manifest.py::test_platform_inventory_candidate_receipt_is_closed_and_preserves_drift`.
  The frozen Platform Core manifest no longer matches the enlarged test set;
  its expected common count is 3076 versus current 3418. This remains an explicit
  integration gap; no assertion or acceptance gate was weakened to save this
  checkpoint. The remaining executed tests reported no failures. Preserve the
  existing classifications when reconciling; do not blindly substitute a count.

That historical source checkpoint had known failing integration checks and was not a stable
candidate ready for Candidate Full or release.
No new native VM, model request, Candidate Full dispatch, merge, tag or release
is part of the checkpoint-save request.
