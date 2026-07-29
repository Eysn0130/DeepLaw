# DeepLaw v0.9.0 installation, upgrade, and rollback

DeepLaw is a local, single-user application. Installation needs no cloud account, model API key,
remote database, or telemetry endpoint. Formal artifacts are attached to the
[`v0.9.0` GitHub Release](https://github.com/Eysn0130/DeepLaw/releases/tag/v0.9.0).

## Verify and install

Download `deeplaw-0.9.0-py3-none-any.whl`, `SHA256SUMS`, and the wheel's
`.sigstore.json` bundle. From the download directory, verify all locally present checksummed files:

```bash
sha256sum --check SHA256SUMS --ignore-missing
```

On macOS, compare `shasum -a 256 deeplaw-0.9.0-py3-none-any.whl` with its line in
`SHA256SUMS`. On Windows PowerShell:

```powershell
Get-FileHash .\deeplaw-0.9.0-py3-none-any.whl -Algorithm SHA256
```

The Sigstore bundle uses GitHub Actions OIDC. Verify the exact workflow and immutable tag identity:

```bash
python -m sigstore verify identity \
  --bundle deeplaw-0.9.0-py3-none-any.whl.sigstore.json \
  --cert-identity \
    https://github.com/Eysn0130/DeepLaw/.github/workflows/release.yml@refs/tags/v0.9.0 \
  --cert-oidc-issuer https://token.actions.githubusercontent.com \
  deeplaw-0.9.0-py3-none-any.whl
```

Verify GitHub provenance when `gh` is available:

```bash
gh attestation verify deeplaw-0.9.0-py3-none-any.whl --repo Eysn0130/DeepLaw
```

Install only the verified bytes:

```bash
uv tool install ./deeplaw-0.9.0-py3-none-any.whl
deeplaw --version
deeplaw doctor
```

The expected version output is `deeplaw 0.9.0`.

## Create a new v0.9 Vault

```bash
deeplaw knowledge init --vault ./vault --name my-project --scope project
deeplaw knowledge autonomy verify --vault ./vault
```

Initialization creates both the retained source-derived compatibility tables and the v0.9
autonomous Markdown/Ledger core. It does not create a mutation grant. Enable `knowledge_sink` only
when an Agent needs durable writes, and grant each non-default operation explicitly.

## Upgrade an existing v0.7 Vault

Stop every CLI, Watcher, MCP, editor integration, and other process that may hold the Vault. Create
and verify the existing v0.7 snapshot first:

```bash
deeplaw knowledge snapshot create \
  --vault ./vault --output ./snapshot-before-v0.9
deeplaw knowledge snapshot verify --snapshot ./snapshot-before-v0.9
```

Install v0.9.0, then create an explicit verified rollback point and install the additive autonomous
core:

```bash
uv tool install --upgrade ./deeplaw-0.9.0-py3-none-any.whl
deeplaw --version
deeplaw knowledge autonomy migrate \
  --vault ./vault --backup ./pre-autonomy-v0.7-backup
deeplaw knowledge autonomy verify --vault ./vault
deeplaw knowledge doctor --vault ./vault --permissions
deeplaw knowledge autonomy rebuild --vault ./vault
```

Migration preserves exact source bytes, reviewed source-derived Assets, ordering, Identity v2,
Inbox provenance, and audit history in a separate compatibility partition. It atomically promotes
the canonical database to `.deeplaw/ledger.sqlite3`, installs STRICT v3 tables, binds legacy source
bytes into the content-addressed repository, and materializes the new workspaces. It does not
convert source-derived authority into Agent-derived authority.

Keep both `snapshot-before-v0.9` and `pre-autonomy-v0.7-backup` until real recall, MCP, integrity,
and owner restore checks succeed.

## Upgrade from v0.6.0

A v0.6.0 Vault must first pass the retained v0.6 → v0.7 control-plane migration, then the v0.7 →
v0.9 autonomous migration. Do not skip either rollback point:

```bash
deeplaw knowledge snapshot create \
  --vault ./vault --output ./snapshot-before-v0.7-control-plane
deeplaw knowledge snapshot verify --snapshot ./snapshot-before-v0.7-control-plane

deeplaw knowledge migrate --vault ./vault
deeplaw knowledge migrate \
  --vault ./vault --apply --backup ./v0.7-control-plane-backup
deeplaw knowledge migrate \
  --vault ./vault --verify --backup ./v0.7-control-plane-backup

deeplaw knowledge autonomy migrate \
  --vault ./vault --backup ./pre-autonomy-v0.7-backup
deeplaw knowledge autonomy verify --vault ./vault
deeplaw knowledge autonomy rebuild --vault ./vault
```

The release gate independently exercises a clean v0.6.0 wheel upgrade into the v0.9.0
distribution and verifies migration, rollback, snapshot, restore, uninstall, and reinstall.

## Roll back the autonomous migration

Rollback is explicit and keeps the replaced v0.9 Vault in a sibling recovery directory:

```bash
deeplaw knowledge autonomy rollback \
  --vault ./vault \
  --backup ./pre-autonomy-v0.7-backup \
  --confirm
```

Verify the restored v0.7-compatible Vault before removing any retained recovery directory. If the
autonomous migration completed and later v0.9 writes were made, rollback intentionally removes
those writes from the active Vault; preserve the v0.9 recovery directory for audit or selective
re-entry.

To restore a full snapshot to a separate root instead:

```bash
deeplaw knowledge snapshot restore \
  --snapshot ./snapshot-before-v0.9 \
  --vault ./vault-restored-v0.7 \
  --confirm
```

Application rollback is separate from Vault rollback. Do not open a v0.9 autonomous Vault with an
older executable. Restore the compatible Vault first, then uninstall or reinstall the application:

```bash
uv tool uninstall deeplaw
```

## v0.9 snapshots and credentials

An autonomous snapshot includes canonical Markdown, CAS objects, the consistent Ledger, staging
and conflict state, Inbox provenance, and capability state. Capability state contains owner-only
tokens. Treat the snapshot as a credential: keep owner-only permissions, never commit or publish
it, and revoke restored grants if custody is uncertain. Derived FTS/vector/graph/Wiki/Canvas data is
excluded and must be rebuilt after restore.

## OCI artifact

`deeplaw-0.9.0-linux-amd64.oci.tar` is an OCI layout archive, not a remotely listening service. Its
manifest digest and archive SHA-256 are bound by `commercial-release-manifest.json` and
`SHA256SUMS`. The image defaults to `deeplaw --version`, exposes no port, runs as `65532:65532`, and
is validated with a read-only root filesystem, `--network none`, all Linux capabilities dropped,
and `no-new-privileges`.
