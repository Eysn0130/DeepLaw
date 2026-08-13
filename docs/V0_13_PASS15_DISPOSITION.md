# DeepLaw v0.13 Pass 15 disposition

Status: **Kernel release/compatibility acceptance contracts synchronized; qualification not
executed; source candidate remains release-blocked** (2026-08-13).

This is a current-fix contract disposition, not a qualification or release report. No real model,
Host installation, authentication state, Secret, RC, GA, tag, merge, or publication was used or
created. Package version remains `0.12.0`, `release_ready=false`.

## Candidate boundary

The exact implementation/documentation candidate before this disposition commit is:

- branch: `codex/v013-pass15-kernel-parity-contract`;
- commit: `804b70b9362d34a42e0897cc119f120ff914be19`;
- tree: `9c7bce05ed1c706ab498b108c8930f5ac0018c7b`;
- Pass 14 base commit: `4da1cbcd90ba7f566d51b85c2a8ed922597f4b87`;
- package: `0.12.0`;
- release readiness: false.

The disposition commit that contains this report is not silently substituted for the exact
candidate binding above.

## Accepted boundary

- PRD `1.3.1` defines the complete v0.13 Kernel distribution and explicitly excludes a first-party
  GUI from v0.13 scope without weakening human/Agent Living Wiki, source-native evidence, Host
  interoperability, or bounded Knowledge Capsule behavior.
- The Qualification Protocol and Traceability Matrix freeze the named, version-bound OpenWiki,
  Tolaria, Obsidian, LLM Wiki behavior-category, Codex, and OpenCode Kernel task map. No generic
  `parity` product capability, database, Knowledge kind, runtime interface, or gate was added.
- Before qualification the only permitted status is that minimum Kernel compatibility parity is a
  release acceptance requirement and has not yet been qualified. Passing all mapped tasks and Core
  gates permits only the frozen v0.13 Kernel compatibility-baseline statement; it does not permit
  product-wide equality, superiority, SOTA, leading, perfect, or fully-verified claims.
- Codex and OpenCode are both Core, required, and release-blocking in active classification v3.
  Claude, Timeline, and semantic restore remain optional/not_claimed. Competitive Claim gates are
  unchanged and remain optional/not_claimed.
- Codex binds the shared Host continuity contract, current App Server public invocation, and
  observed `0.147.0-alpha.1.2` tool version. OpenCode uses the same raw contract and remains
  blocked: no installed version was invented, and its null version constraint is an unresolved
  candidate-binding prerequisite rather than a wildcard.
- Native real-Host qualification is separate from the nine-row Linux/macOS/Windows artifact gate.
  Success on one macOS Host cannot replace three-OS artifact evidence, and one Owner credential is
  not required on all three operating systems.
- Core Living Wiki compile/project/read/navigation/edit/reconcile bindings are Core/Active.
  Canvas, communities, centrality, and visual graph analytics remain Driver/Hidden; third-party
  Obsidian/Tolaria desktop surfaces remain deferred without downgrading underlying file behavior.
- `experimental.real_hosts` was removed because qualification execution is evidence state, not a
  product surface. Codex/OpenCode connection remains represented by the existing Driver surface.

## Active and historical contracts

Active v0.13 qualification references are:

| Contract | SHA-256 |
| --- | --- |
| `docs/PRODUCT_REQUIREMENTS.md` | `101f7af07837d0370da60656f10386ae046d55974e73a84db0dace0700cfacfc` |
| `benchmarks/release/v013-gate-classification-v3.json` | `c09209112e8656fc62be4b535cc93b092bc3ef2a1818418f7ffcfe40a7879e0a` |
| `contracts/v013-release-gate-classification.v3.schema.json` | `89a75e066ab83adb5e56108e1548fdb21e2dd57dc3fd7a4b64c4307a1ca0cbbf` |
| `contracts/provenance-bound-gate-result.v2.schema.json` | `944bf8c605b5346590b3f8ba076ebdf0cc61523cc10c364cedbd2f6b19f782fc` |
| `contracts/commercial-evidence-report.v3.schema.json` | `9bd2abead426ec2c5e1c70b1efe21e464e3a05e8fa3f74f61a89432509b80188` |

The following historical bytes were not rewritten:

| Historical evidence | SHA-256 |
| --- | --- |
| `benchmarks/release/v013-gate-classification-v2.json` | `4efbb8096f0fc57fbb8cc1ffe76e794e3bc6022b0969d1d980dfc80c112a90e2` |
| `contracts/v013-release-gate-classification.v2.schema.json` | `050ab23c714e65e8ffd0121de975c012e1ea4ff148f294c47f77f900c0c67ef9` |
| `benchmarks/v013/qualification-protocol-v1.json` | `95283e2d1fdd60a429941c6ab718cebd739ad414ddc38d58b3f2fcc14f4cffb5` |

`benchmarks/v013/qualification-protocol-v1.sha256` still records that exact protocol digest and
filename. The machine protocol already expressed external gates, so no protocol-v2 was created.

## Fail-before and pass-after

The new Pass 15 regression first produced `5 failed, 1 passed`, observing all requested stale
states: no active v3 pointer, OpenCode optional, obsolete Codex Host input/invocation, Living Wiki
bundled with graph analytics, README_EN claiming a stable core, and PRD current status pinned to
Pass 8. The historical-byte protection test passed in that failing run.

After the minimum corrections, the focused qualification/provenance/product-outcome/product-
surface/reproducible-source tests passed. The first complete suite then reported `1583 passed, 6
skipped, 5 failed`: four failures were the intentional README_EN source change tripping the
repository-visible development Gold hash, and one was the reproducible builder excluding newly
created but not-yet-tracked contracts. The development-only source hash was rotated using the
repository's existing practice, the new contracts were committed, and the five exact regressions
passed before the complete suite was rerun.

Final verification:

```text
uv lock --check
  Resolved 140 packages in 3ms
uv run pytest
  1588 passed, 6 skipped in 486.80s
uv run ruff check .
  All checks passed!
git diff --check
  passed with no output
```

The six skips remain explicit non-results and are not promoted into qualification evidence.

## Remaining release blockers

Minimum Kernel compatibility parity remains unqualified. Codex isolated official interactive login,
an exact installed OpenCode version and qualification-only dotenv, both Hosts' three real task
families, independent Human Gold, exact legal evidence, Context Utility, scale, native three-OS
artifact qualification, provenance/signing/public-redownload, and the complete artifact chain
remain open. Active classification assembly remains disabled; package stays `0.12.0`,
`release_ready=false`; no tag or release is permitted.
