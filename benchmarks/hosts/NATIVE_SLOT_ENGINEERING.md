# Native slot engineering candidate

Status: engineering candidate, not formal Host admission. The current producer
components retain `formal_admission=false`; they do not replace the existing
qualification contracts or admit a new Host/package identity.

## Observed native boundary

The macOS launcher creates a Linux ARM64 VM with no network device, disk, shared
directory, or USB controller. One VSOCK connection is handed to the same local
owner through a private UNIX socket. The guest launches Host UID 1000 and MCP UID
1001 in separate mount, PID, network and IPC namespaces, with fresh roots,
read-only runtime bindings, cgroups and seccomp. The owner-side control client
uses only health, new-session, MCP-status, fork and stop operations.

A zero-model engineering run has exercised those five routes through the VM
external connection. The real Host and MCP both exited 0, both role cgroups were
empty, and the VM reported a normal guest stop. This used the candidate benchmark
modules with the previously frozen `f5d7b54` wheel. It is not evidence for a new
final candidate or a formal slot.

The startup readiness budget is 15 seconds, including the single health HTTP
request. Other HTTP requests remain bounded by 5 seconds. Health retries only
TCP connection failures before a request could be sent; a possible send or
response timeout terminates the preflight. Session creation and fork are never
retried. The owner checks the
unchanged launcher receipt digest and actual role exit/cgroup fields. Additional
process observations, when requested, travel as a separately hashed receipt.

## Owner preflight entry point

Prepare and approve the native launcher, Linux kernel and closed guest initrd
outside the candidate's authority. The initrd must contain the exact frozen Host,
Python runtime, wheel inputs, guest control code and isolated MCP setup. The
repository includes a deterministic engineering builder with a closed manifest,
fixed module destinations and an approved wheel inventory. It does not yet
provide a complete installed formal broker or an admitted construction profile.

```sh
uv run python -m benchmarks.hosts.build_native_preflight_initrd \
  --manifest /absolute/path/to/approved-manifest.json \
  --manifest-sha256 EXPECTED_SHA256 \
  --destination /absolute/path/to/new-private-build-directory
```

The manifest binds the base initrd, Host binary, wheel bytes and all registered
guest modules, plus the engineering run and candidate identifiers. The runtime
installs only wheels named in the generated inventory and checks each digest;
unlisted wheels inherited from the base are not selected. The result receipt
records logical input names and hashes without exporting source paths.

```sh
uv run python -m benchmarks.hosts.native_slot_owner_preflight \
  --native /absolute/path/to/approved-native-launcher \
  --native-sha256 EXPECTED_SHA256 \
  --kernel /absolute/path/to/approved-kernel \
  --kernel-sha256 EXPECTED_SHA256 \
  --initrd /absolute/path/to/approved-initrd \
  --initrd-sha256 EXPECTED_SHA256 \
  --destination /absolute/path/to/new-private-result-directory \
  --require-process-observation \
  --require-boundary-observation \
  --require-fork-observation \
  --require-execution-observation \
  --expected-host-sha256 EXPECTED_HOST_SHA256 \
  --execution-context /absolute/path/to/approved-execution-context.json \
  --execution-context-sha256 EXPECTED_CONTEXT_SHA256
```

The destination must not already exist. Inputs are copied into it with digest,
regular-file and size checks; symlinks and FIFOs are rejected. Keep the path short
enough for the platform's UNIX socket path limit. The command uses two vCPUs,
2048 MiB memory and a 90-second VM bound. It does not load credentials or issue
model requests. Failure observations and native lifecycle output remain in the
new result directory; a VM exit 0 alone does not imply successful Host cleanup.

The execution context is a closed object containing `run_id`, `candidate_id`,
and `candidate_binding` (`commit`, `tree`, `lock_sha256`, `wheel_sha256`,
`sdist_sha256`). Its run/candidate identifiers must equal the initrd's owner
input. The owner hashes this context, its immutable launcher/kernel/initrd
copies, the expected Host bytes and a fresh challenge. `bind_execution` must be
the first control operation and may occur only once. Both actual executable
snapshots carry that exact binding; the owner rejects a different binding.
Execution observation is mandatory for this owner entry point. The initial
binding reply has a 15-second budget, covering the guest's bounded unused-MCP
closure and executable hashing. Each snapshot's process-start commitment must
equal the original launcher role row, so another process in the same namespaces
cannot substitute for the launched role.

The root observer opens the live `/proc/PID/exe`, hashes bounded bytes, and
checks the process start identity, UID, namespaces and executable inode before
and after the read. The Host snapshot runs after its actual ready line and
before health HTTP; the MCP snapshot runs after the owner challenge and before
an unused MCP receives EOF. This is an engineering execution snapshot, not
formal native authority or a complete lifecycle/credential observation.
MCP status establishes connectivity only: `mcp_exercised=false` until an actual
functional call is separately observed. The new snapshot/challenge path has
portable regressions. The 2026-10-02 reconstructed inputs have entered real
zero-model engineering control. Two retained owner calls used the fork-only
guest with the normal MCP-status sequence and failed on the empty MCP response;
the corrected third call used the matching fork-only and route flags and passed.
That exact run observed Host/MCP root exits 0, empty role cgroups, normal VM exit
0, and three loopback connections with zero kernel drops. Its observation digest
is `8ecc7c3988ed162f69282eef2a51d1fad87fd8df700831a65716331e07794d5a`;
the initrd digest is
`12392d2511b89c7a20e9340b5df24f17b0d8307d3ea72426c815d4e3900829e0`.
The wheel remains the exact `c5b55d7` checkpoint, separately bound from the newer
guest benchmark modules. Current formal native acceptance remains unexecuted.

The subsequent `native-mcp-preflight-1` used the normal MCP-enabled Host entry.
It observed a connected MCP status and four closed control connections: health,
new session, MCP status and fork. Owner failure and failure stage were null,
the VM exited 0, and the original observation SHA-256 is
`4eb894d1a6d0a3a4ef41254197235ae4414bd00bcf41c7afa0501da839745d3d`.
Its initrd SHA-256 is
`f89a54d5472502554a2fb4a9496fb7c7285d8d01140e7b70069224f92bb31f00`.
The checkpoint wheel and newer guest modules remain separate inputs;
`mcp_exercised=false`, model invocations 0 and `formal_admission=false` remain
literal. MCP connectivity does not establish functional tool-call acceptance.

## Explicit fixed model probe

The successor `deeplaw.native-preflight-build/v2` manifest enables only
`native_model_probe`, with one explicit dummy Provider nonce. The zero-model v1
entry rejects forwarding. `run_model_probe` separately requires the process,
boundary, fork, route and executable observations before it can start. The guest
holds the dummy nonce; actual credential authority stays in the local owner.
This engineering entry still returns `formal_admission=false` and makes no
functional MCP claim.

The optional `bind_authority` callback receives the exact execution-binding
digest only after the guest confirms that fresh challenge. It is available only
to the explicit model entry, and a callback failure closes the native slot before
health or Provider dispatch. The zero-model entry rejects authority-hook injection.

The fixed public probe selects `deepseek/deepseek-v4-flash`, denies tools and
limits output to 256 tokens. The frozen Host config schema requires both model
context and output limits; its context limit is the public snapshot's 1,000,000.
The guest's HTTP proxy is confined to 127.0.0.1:4100. A single VSOCK reader accepts
one bounded Provider frame, and consumes its sequence before forwarding. Requests
are not replayed after ambiguous sends. RequestGuard inspects the complete Host
request before the owner can issue HTTPS. Its body, chunk metadata and trailer
reads use an absolute deadline; the urllib opening phase has a socket timeout
and does not by itself establish a forcibly interruptible wall-clock bound.

The proxy binds its literal endpoint through TCPServer, then sets HTTP server
name/port metadata. This avoids HTTPServer's implicit reverse DNS lookup while
preserving the existing loopback address, port and request policy. A real local
socket regression forbids resolver calls and verifies closure and rebinding.

The model route observer uses a closed v2 profile: 4096 control requests remain
separate from the single permitted 4100 auxiliary flow. Unknown ports, capture
loss and open flow windows remain gaps. Auxiliary fields contain client byte
counts, hashes and TCP closure only; they do not prove Provider response body
completeness. This profile has a fixed 190-second capture budget, while the
zero-model profile keeps 60 seconds. The owner requires the matching profile.

A bound completed assistant with positive output or reasoning usage establishes
engineering model execution. Request admission, HTTP success, a session ID or
an acknowledged prompt alone does not. A missing observation remains unknown.
The owner result records `credentials_supplied=null` for a model callback because
callback availability cannot establish external credential loading; the zero-model
entry records `false`.
The fixed reply's success is evaluated separately. Failure diagnostics classify
one bounded isolated Host log into fixed public categories and do not export
log text, exception payloads, credentials or local paths.

The dry probe's final finite classification was `host_log_model`. The frozen
models.dev snapshot marks `deepseek-v4-flash` deprecated, which the exact Host
provider implementation excludes. The [DeepSeek change log](https://api-docs.deepseek.com/updates/)
records retirement of V4 Flash on 2026-09-10 and temporary routing of that old
name to V4.1 Flash. The current selector must therefore remain unexecuted until
the acceptance identity is explicitly refrozen; changing catalog status cannot
restore the retired model. No actual Provider request has been issued.

Observer completion now consumes an already-emitted bounded route gap even if
the capture process closed its control pipe first. It neither repeats control
requests nor substitutes a generic cleanup gap for the original capture failure.
The pure Provider response observer independently checks JSON or complete SSE
model fields in memory and returns finite metadata and body hashes. Host config
and request selectors cannot establish the Provider response identity.

The older formal isolation source describes a Secret-bearing Host parent and a
Secret-free MCP child. The current engineering guard topology instead gives the
Host a nonce and confines the actual key to the external owner. Its ambiguous
`parent_secret_present` and `*_read` flags cannot establish this different
topology by relabelling the owner. Checking a request in memory is reading it
even when no raw body is retained.

A pure successor isolation source validator now checks explicit actor bindings:
credential authority, Host, MCP, native observer, runner and scorer. It separates
key delivery, public in-memory request/response inspection, raw-body retention,
and reads of private Host stores. The credential-authority process must differ
from every business observer and runner process. Source/instance/process hashes,
fresh challenge, exact execution binding and native receipt must all bind the
same candidate and run. Missing observations remain gaps. This source remains
engineering-only until the independent native collector cross-checks those
bindings; the old source and its admission rules remain unchanged.

`owner_provider_authority` supplies a separate engineering IPC process for the
existing RequestGuard callback. Its fixed profiles are one request/120 seconds
and an explicitly selected maintenance profile of six requests/180 seconds;
timeouts may be shortened. The parent consumes each sequence before sending,
the child consumes it before forwarding, and ambiguous attempts are not replayed.
Nonblocking frame deadlines and a parent watchdog end blocked forwarding. Cleanup
must terminate and confirm the created process group even if its leader already
exited. Cleanup is consumed once per actual child; subsequent terminal calls never
signal or observe its old PID or process group. Entry-script hashes and Popen
commitments require serialized immutable inputs and are not native execution
identities. This module loads no credential;
the private owner entry must keep credential loading inside the authority child.

The local `native-authority-dry1` engineering run accepted the guest execution
challenge before starting this authority process, and the two binding hashes
matched. The child loaded no credential, made zero outbound attempts and closed
with its process group confirmed empty. The VM exited zero, while the overall
probe remained failed: `host_log_model` and `route_capture_ipv4_route_gap` were
retained, model execution was not observed and invocation count remained unknown.
Its original observation SHA-256 is
`1ade7ee72a4cd3af91007a5f93d0d3a123cf4dc817ae8849f67f47d41e91c685`.
This proves the engineering IPC connection and cleanup only; it does not establish
functional MCP, native isolation Authority or formal model-task admission.

A bounded private route diagnostic observed a 68-byte loopback IPv4 UDP packet
to port 53. A controlled HTTPServer bind reproduced its reverse DNS call; the
minimal literal-bind candidate eliminated that call. The private
`native-proxy-dns-fix1` VM then validated four closed control routes, zero kernel
drops, no auxiliary flow, closed role processes and empty cgroups. Its authority
binding matched the guest execution binding, and its child/group cleanup was
confirmed. The original observation SHA-256 is
`ee69550287336988305d719aa62a476fb61eebe194a89cc66176f20f6c1ba5ec`,
with initrd SHA-256
`48bbc47214e930185eb5441cd44f2f69cb2a41772e26dd4074e9bec8776f93a7`.
No credential was loaded or Provider request attempted. The model task remained
unobserved and the overall failure was `model_probe_not_observed`. Its private
candidate module and checkpoint wheel bindings do not establish acceptance of
the later public source candidate. The earlier dry run's original
`credentials_supplied=true` was a callback-availability bug; it cannot establish
that a credential was loaded. The corrected result leaves that field null.

`maintenance_bundle_attachment` copies only the original files actually consumed
by the fixed 15-slot public run validator into a fresh
`attachments/c6_public_maintenance` subtree. It checks source and copied identities,
reopens the destination, preserves failed/unknown scores and generates only the
45 fixed empty fixture directories. It neither scans unused source areas nor
copies private keys, capabilities or staging contents. Its closed receipt retains
`formal_admission=false`; Kernel attachment admission is still a separate gap.

The isolated installed-Host loader test received a real `session.created` event
even when its fake local resolver was missing. Subsequent diagnostic runs and
the unchanged test rerun passed, leaving the first resolver failure unclassified.
The test now disables unrelated model-catalog acquisition and independently
binds the public event and resolver to the same session. It retains the original
deadlines and reports the event/resolver distinction on failure. A later complete
run reproduced the resolver failure. Temporary copied-plugin stage observations
showed successful event recording and spawn, followed by a child timeout before
the fake Python resolver's first statement. The ambient PATH selected the macOS
developer-tool Python shim. A paired diagnostic changed only the shebang to the
test interpreter and observed child exit zero. The public fixture now selects
and asserts that same interpreter, without changing the production adapter. A
local loader
pass for installed version 1.18.16 does not establish the reconstructed `.3`
source identity or formal native acceptance.

The later complete run still failed to observe the resolver with that exact
interpreter, so this change alone did not close the intermittent failure. A
copied-plugin relative/absolute command pair observed the expected PATH and fake
executable; it did not demonstrate a PATH fault. The loader fixture now requires
a bounded explicit fake-CLI preflight before Host startup, without populating
either Host oracle file, and records the actual fake's earliest Python entry.
It also disables update/default-plugin/Claude work as in the native closed profile.
Original event, argv, environment and 3/5/15-second assertions remain. Cache warming
from the prerequisite and a focused pass cannot establish cold-start acceptance
or prove the intermittent root cause.

The latest complete run failed earlier, in that direct fake-CLI prerequisite's
three-second timeout, before any OpenCode server was started. The prerequisite
skips both oracle files, so their absence cannot show which Python stage stalled.
One bounded direct/staged-import comparison with the same four environment
entries, cwd and deadline completed in less than 0.14 seconds per case. It did
not reproduce the complete-run failure or establish a common cause with the
earlier resolver failures. The failing complete check is preserved; no further
full retry or deadline expansion was made. The `.3` engineering archive was
separately reopened and passed static package validation against its exact
executable bytes; this remains engineering input evidence.

## Remaining qualification work

A later zero-model native run collected 106 process-connector events with closed
sequence windows on both vCPUs. Its role trees contained eight Host nodes and
three MCP nodes, each with observed exits; both roots exited 0 and both cgroups
were empty. These are observations of that exact engineering run.

The repository builder and installed collector subsequently reproduced that
boundary in `ctrl011-installed-preflight-1`: 106 events, eight Host nodes and
three MCP nodes with all role exits observed, root exit codes 0, empty cgroups
and normal VM exit 0. The initrd digest was
`b447c70e4753bf0b423773120a986593306a9bc937c2ff2f18824344ad66c959`.
It still used the retained `f5d7b54` wheel and invoked no model or credential.

`ctrl011-boundary-preflight-2` additionally observed four entry-phase negative
attempts in the same role processes: both roles received `EACCES` when opening
the fixed runtime entry for writing, and `ENETUNREACH` when connecting to the
fixed non-loopback test address. The external audit collector correlated all
four actual SYSCALL records with role PID/UID and errno; lost and backlog were
zero at both ends. Host/MCP again exited 0 with empty cgroups, and the VM stopped
normally. The exact initrd digest was
`9c39c7327122dc2f5edc33b771f0f03ea2dba85c7bb836de63bcdd74440f41fe`.
This is a bounded negative probe, not a complete runtime filesystem/network
trace. In particular, `EACCES` does not establish an `EROFS` result.

The initial attempt and diagnostic failures remain retained. Linux syscall
audit must be enabled before role fork: `audit_alloc()` skips tasks created
before `audit_ever_enabled`. The guest therefore enables audit first, then
installs the bounded UID/failure rules after both isolated entry gates are
ready. See the [Linux v6.18 audit implementation](https://github.com/torvalds/linux/blob/v6.18/kernel/auditsc.c).

The subsequent `fork-2` engineering observation correlated the original public
fork response with one new child plugin event. The owner control reply was sent
after that event was observed (10 ms observation wait); this does not establish
that the upstream HTTP response itself was delayed. The same run retained the
four boundary negatives, closed process windows, both role exits 0, empty
cgroups, and VM exit 0. Its initrd digest was
`74d5ba472bdf9efa242f50ac9128b28591237354f6b364ca45eb3851639edc73`;
the observation file digest was
`217b0afbc53870a22ff4472b2ce4b54c49b81ef5ad5d05f104b6320c54a349fa`.
The failed `fork-1` attempt remains retained: an owner transport timeout option
was incorrectly included in the closed guest request and was rejected. The
corrected client has a socket-frame regression. Both runs used the same frozen
guest bytes, with the corrected owner client only in `fork-2`.

Exact candidate/run/nonce and Host package binding, credential isolation,
Provider forwarding, remaining permission negatives and existing formal receipt
validation still have to close before the first slot can be admitted. No
intermediate status or configuration hash substitutes for those observations.

The passive `linux_http_route_observer` reads cooked AF_PACKET traffic in the
Host network namespace. It retains bounded request bytes only in memory and
exports fixed route categories and hashes. It rejects capture loss, unsupported
protocols/framing, incomplete TCP windows, sequence gaps, conflicting
retransmissions and budget overflow. Its scope is the isolated loopback, not
non-network model execution. See [packet(7)](https://man7.org/linux/man-pages/man7/packet.7.html)
for cooked capture and packet statistics semantics.

`route-1` captured 164 packets with zero kernel drops, including outgoing
loopback duplicates. It observed two health requests followed by new-session,
MCP-status and fork; the strict four-request sequence check failed. The duplicate
health requests are retained, not collapsed. `route-2`, after making health
non-replayable once connected, failed with `guest_host_http_timeout`. The native
launcher then exited 0 in response to the owner's cleanup signal; that is not
a normal guest shutdown. Neither attempt establishes formal preflight admission.

`route-3` narrowed the failure to response headers timing out after TCP connect
and request send. The frozen upstream implementation listens before attaching
its request handler. Waiting for its actual ready line before the single health
request produced `route-4`: 88 packets, zero drops and exactly health, session,
MCP-status and fork. The source ordering is observed; its causal connection to
the earlier timeout remains inferred. The ready line is a startup gate only;
the actual health response and independent packet capture remain required.

For the closed zero-model fork sequence, set the manifest purpose to
`zero_model_fork_preflight` and pass `--fork-routes-only` together with
`--require-route-observation`. This purpose configures no Host MCP entry. The
guest root verifies the separate MCP socket peer UID and closes it with EOF
without sending a request. `three-route-1` then captured 68 packets, zero drops
and exactly three connections for health, session and fork, bound to the actual
Host network namespace. Both role roots exited 0, cgroups emptied and the VM
stopped normally. Its initrd digest was
`32a9ddad286ad01c3e28c785336badd723e9997bf47847f05338efd1d20ba352`.
It retained the four entry negatives and closed role process windows, but
`mcp_exercised=false` and `mcp_connected=null`: no Host-to-MCP functional claim.

These results still lack a final candidate/run/challenge binding and a complete
external broker response. Loopback route counts cannot supply unknown internal
model invocation, Provider, plugin or forwarding counters. The optional private
fork-source transport preserves original bytes inside guest root memory for the
existing adapter; its regression checks do not establish native or formal
admission. Closing the controller drops those references without claiming secure
memory erasure.
