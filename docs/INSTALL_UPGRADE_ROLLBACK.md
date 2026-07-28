# DeepLaw v0.7.0 installation, upgrade, and rollback

DeepLaw is a local, single-user application. Installation requires no cloud account, model API
key, remote database, or telemetry endpoint. The official artifacts are attached to the
[`v0.7.0` GitHub Release](https://github.com/Eysn0130/DeepLaw/releases/tag/v0.7.0).

## Verify and install

Download `deeplaw-0.7.0-py3-none-any.whl`, `SHA256SUMS`, and the wheel's
`.sigstore.json` bundle. Verify the checksum from the download directory:

```bash
sha256sum --check SHA256SUMS --ignore-missing
```

On macOS, compare `shasum -a 256 deeplaw-0.7.0-py3-none-any.whl` with the wheel line in
`SHA256SUMS`. On Windows PowerShell, use:

```powershell
Get-FileHash .\deeplaw-0.7.0-py3-none-any.whl -Algorithm SHA256
```

The Sigstore bundle uses GitHub Actions OIDC. With the `sigstore` CLI installed, verify the wheel:

```bash
python -m sigstore verify identity \
  --bundle deeplaw-0.7.0-py3-none-any.whl.sigstore.json \
  --cert-identity https://github.com/Eysn0130/DeepLaw/.github/workflows/release.yml@refs/heads/main \
  --cert-oidc-issuer https://token.actions.githubusercontent.com \
  deeplaw-0.7.0-py3-none-any.whl
```

Verify GitHub provenance when the GitHub CLI is available:

```bash
gh attestation verify deeplaw-0.7.0-py3-none-any.whl --repo Eysn0130/DeepLaw
```

Install the verified bytes:

```bash
uv tool install ./deeplaw-0.7.0-py3-none-any.whl
deeplaw --version
deeplaw doctor
```

The expected version output is `deeplaw 0.7.0`.

## Upgrade from v0.6.0

Create and verify a local Vault snapshot before changing the application:

```bash
deeplaw knowledge snapshot create --vault ./vault --output ./snapshot-before-v0.7
deeplaw knowledge snapshot verify --snapshot ./snapshot-before-v0.7
```

Upgrade the application, then plan, apply, and verify the additive migration:

```bash
uv tool install --upgrade ./deeplaw-0.7.0-py3-none-any.whl
deeplaw --version
deeplaw knowledge migrate --vault ./vault
deeplaw knowledge migrate --vault ./vault --apply --backup ./v0.7-migration-backup
deeplaw knowledge migrate --vault ./vault --verify --backup ./v0.7-migration-backup
deeplaw knowledge doctor --vault ./vault --permissions
```

Migration preserves source fragments, reviewed state, ordering, and audit history. It is additive
and fails transactionally.

## Roll back a Vault migration

Keep the verified backup until the upgraded Vault has completed normal recall and integrity checks.
To roll back the migration in place:

```bash
deeplaw knowledge migrate --vault ./vault --rollback \
  --backup ./v0.7-migration-backup --confirm-rollback
```

To preserve the current Vault and restore the earlier snapshot to a separate root:

```bash
deeplaw knowledge snapshot restore --snapshot ./snapshot-before-v0.7 \
  --vault ./vault-restored-v0.6-state --confirm
```

Application rollback is separate from Vault rollback. Uninstall v0.7.0 only after the Vault state
is compatible with the intended older application:

```bash
uv tool uninstall deeplaw
```

## OCI artifact

`deeplaw-0.7.0-linux-amd64.oci.tar` is an OCI layout archive, not a remotely listening service.
Its manifest digest and archive SHA-256 are in `commercial-release-manifest.json` and
`SHA256SUMS`. The image defaults to `deeplaw --version`, declares no exposed port, runs as
`65532:65532`, and is validated with a read-only root filesystem, `--network none`, all Linux
capabilities dropped, and `no-new-privileges`.
