# Candidate OpenCode lifecycle build

Status: engineering candidate. These files preserve the MIT-licensed source patch
and exact build identity used for zero-model native lifecycle checks. They do not
establish formal Host qualification and do not replace historical OpenCode
1.18.16 evidence.

The zero-model package validator accepts the recorded Linux shutdown build only
when its explicit version, source commit and executable SHA match this manifest,
and the package bytes match the owner-frozen Host identity. Other patched builds
remain rejected. This is static input admission, not formal observation
authority; the Codex and OpenCode qualification model identities are unchanged.

Apply `opencode-1.18.16-deeplaw.patch` to upstream commit
`a3647eb025c7615159d417dcc49fc39fdaeba65b`. The patch adds bounded SIGTERM/SIGINT
listener shutdown and an explicit Linux ARM64 musl cross-build target. Shutdown
failure remains a nonzero Host exit; the parent VM exiting normally is a separate
observation.

Build inputs are recorded in `opencode-1.18.16-deeplaw-build.json`. Use Bun 1.3.14,
the unchanged upstream `bun.lock`, the real frozen models.dev snapshot, and the
exact locked target-native packages. The Linux compilation additionally uses the
verified Bun Linux aarch64 musl executable. From `packages/opencode`:

```sh
OPENCODE_VERSION=1.18.16-deeplaw.2 \
OPENCODE_CHANNEL=deeplaw-local \
MODELS_DEV_API_JSON=/absolute/path/to/frozen-models.json \
bun run script/build.ts --target=linux-arm64-musl \
  --compile-executable-path=/absolute/path/to/bun-linux-aarch64-musl \
  --skip-install --skip-embed-web-ui
```

`OPENCODE_RELEASE` must be absent: upstream checks its presence to enable release
upload. The local lifecycle build does not publish an upstream release.

The 2026-10-02 reconstruction uses the separate
`opencode-1.18.16-deeplaw.3-build.json` identity and version
`1.18.16-deeplaw.3`. The historical models.dev snapshot was unavailable; the
new public snapshot is frozen at
`05ff2f1a0cc1623171c6ac7988fc71fb2359701997fafe620140b0f1610e56c4`.
The reconstructed parentless source commit has the same patched source tree
as the historical build, but a distinct commit and binary identity. The original
record remains historical. Neither static package admission nor successful
compilation establishes a completed model task or formal Host qualification.

The `.3` record also binds an owner-local engineering archive at
`dc5900223d2725ef43fbf34d871a739233468b8385e7417ab7015641dcb61a9f`.
Its sole regular member, `bin/opencode`, was independently reopened and matched
the recorded executable digest. Generator versions and fixed archive parameters
are retained. This archive passed the existing static package validator; it has
not been installed as a formal qualification identity or published as a release.

The closed qualification environment sets `OPENCODE_DISABLE_MODELS_FETCH=1` to
prevent background model-catalog acquisition. A real offline Linux guest exposed
a shutdown block with refresh enabled; disabling it let the same fixed Host
binary become healthy and exit 0 on SIGTERM without force-killing. This observation
does not claim a general fix for every upstream background-refresh shutdown path.

The Python/Objective-C platform observers remain intermediate evidence producers.
Formal admission still requires the separately bound candidate, exact package,
Host identity, broker, native events, permission negatives, and complete cleanup
observations.

The isolated Linux Python probe uses the unchanged locked dependency versions.
`linux-python-wheel-builds.json` records three aarch64 musl source builds. The
Java 0.23.5 and TypeScript 0.23.2 sdists omit required headers; their corresponding
official tag commits supply the recorded MIT-licensed header bytes. Existing C
source bytes remain unchanged. These locally built `linux_aarch64` wheels are
not relabelled as manylinux artifacts. The completed source archives, original
sdists, build receipts and wheel bytes must remain separate, hash-bound inputs.

`linux-python-wheel-builds-20261002.json` records the new offline source builds
under Python 3.12.15. The matching older development APK was unavailable, so
the runtime and headers were frozen together at the newer patch version. Locked
DeepLaw dependency versions remain unchanged. All three actual imports succeeded
in a VM with no network device, and the VM exited normally. These wheels retain
their `linux_aarch64` tags.

The reconstructed Alpine 6.18.52 kernel is an EFI zboot gzip wrapper. The native
Linux boot loader uses its extracted ARM64 `Image`, recorded as a separate
derived artifact with original digest, bounded header offsets, official format
source and output digest. Passing the wrapper directly failed VM startup; the
same launcher and initrd succeeded after this format correction.
