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
