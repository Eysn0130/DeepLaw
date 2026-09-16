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
  --require-fork-observation
```

The destination must not already exist. Inputs are copied into it with digest,
regular-file and size checks; symlinks and FIFOs are rejected. Keep the path short
enough for the platform's UNIX socket path limit. The command uses two vCPUs,
2048 MiB memory and a 90-second VM bound. It does not load credentials or issue
model requests. Failure observations and native lifecycle output remain in the
new result directory; a VM exit 0 alone does not imply successful Host cleanup.

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
