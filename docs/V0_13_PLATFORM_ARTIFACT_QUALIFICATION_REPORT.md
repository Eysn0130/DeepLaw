# DeepLaw v0.13 platform and source-candidate artifact qualification

Status: **historical local Darwin/package evidence plus a Pass 14 exact-wheel preflight; 3-OS and
release lifecycle gates remain unmet** (2026-08-13).

The original 2026-08-08 candidate record below remains historical. It is not silently rebound to
the current implementation.

## Candidate binding

The qualified implementation commit is
`bb6a942970186f03ea41e108a2eceaaca54e3bcb`, tree
`8817db9349b504784b95690844ee10f43769cbdd`, package version `0.12.0`. The frozen
qualification protocol SHA-256 is
`95283e2d1fdd60a429941c6ab718cebd739ad414ddc38d58b3f2fcc14f4cffb5`.

## Darwin Python matrix

Each lane used the locked `dev` and `discovery` dependency sets and the exact command form:

```bash
uv run --isolated --python <3.11|3.12|3.13> \
  --extra dev --extra discovery --frozen \
  python -m pytest --strict-markers --junitxml=<external-junit.xml>
```

| Runtime | Result | Duration | External JUnit SHA-256 |
| --- | --- | ---: | --- |
| CPython 3.11.15 | 1,147 passed; 9 skipped; 0 failed/error | 267.50 s | `c02888cbed53fa9f2317ffb13fdfee7413fed264513e8f426bcdbaddf19668ee` |
| CPython 3.12.13 | 1,147 passed; 9 skipped; 0 failed/error | 277.24 s | `193c6602503e81656df56fed7e7b5b503c686b1dce4141d61de4205211ae4c49` |
| CPython 3.13.13 | 1,147 passed; 9 skipped; 0 failed/error | 286.60 s | `f2788f01c86ad21c6a16564bb44cbfe7577c48487a3d5fa103e58c3e1288dc4a` |

Each JUnit contains 1,156 collected tests: 1,147 passed and 9 explicitly skipped. JUnit files are
external diagnostic artifacts and are not committed. The 9 skips remain visible and are not pass:
they cover declared historical-wheel, native Windows ACL/junction and v0.13 external/manual gates.

Linux and Windows were not available. Consequently the required 3 OS × Python 3.11/3.12/3.13
matrix is `not_executed`, not inferred from Darwin.

## Reproducible package and inventories

`benchmarks/v013/reproducible-build-source-candidate-2026-08-08.json` binds a clean worktree,
244 JSON contracts, the lockfile and migration identities. Its file SHA-256 is
`40f4fd4b2cb077732eb1d82e56919cc8f5f23786646db17015cf2b99339e92fb` and its internal
record SHA-256 is `5bf510a7ed88ef0d07399799173759f49808a3e48fe9224be4c65b5e3e85b5dc`.
Two isolated builds were byte-identical:

| Artifact | Bytes / paths | SHA-256 |
| --- | --- | --- |
| `deeplaw-0.12.0-py3-none-any.whl` | 1,137,529 / 354 | `5d55867e13f5e9fb212591eda67d6f36357db2a84c44c2722fd11665a9d17206` |
| `deeplaw-0.12.0.tar.gz` | 23,274,339 / 1,566 | `e0a49ba55510f2fabc9a8b8bca81253d5b298b32baa95bb3f7304f8702703489` |

A fresh locked wheel environment passed CLI/import version checks, packaged Query v6/provider
capsule/query-audit contract presence and dependency compatibility. The generated CycloneDX 1.5
SBOM contained 128 components and had SHA-256
`c149ef8118e8bd9b76fc443f81e959022bf742c1d455ac2f279f53afe543d59d`.
The installed-license inventory passed for 115 packages; its file SHA-256 was
`10e7972eeb6277eb17231ed1f3c6da9095cc88510c6374e4dcfbf5cdf76aa2e4` and record SHA-256
was `06e296d6ca756e8afb4dc2d54560e77125c278b8bbe35b28034319d1f7036caa`.

The wheel, sdist, SBOM and license inventory remain external build evidence because no release is
authorized. They were not signed or uploaded.

## Missing release lifecycle evidence

- Linux and Windows matrix lanes: `not_executed`.
- Formal release provenance/signing and public redownload byte verification: `not_executed`.
- A v0.13 version, tag, RC, GA, catalog or public artifact: absent by design.

The reproducible-build tool's local artifact result does not override the product release gate.
`release_gate_passed=false`, `competitive_claim_eligible=false`, and package version remains
`0.12.0`.

## Pass 14 current implementation artifact addendum

The clean Pass 14 implementation candidate before evidence documentation was commit
`e81e9c87e4215de2d26d354051a20678fd9a4ca8`, tree
`3ff5c44fdbcf0fb1e698f422fa4533e4fee83443`. A fresh constrained wheel build and isolated public
journey executed outside the repository:

| Artifact / receipt | Result |
| --- | --- |
| `deeplaw-0.12.0-py3-none-any.whl` | 1,265,613 bytes; SHA-256 `eb7e77c89a63ee5781c1b57714fcc8d0702e582f6e22dc1ead8611bb7aa08aad` |
| `fresh-wheel-journey.json` | valid; SHA-256 `1f91299046a88796348146e78c86c2fbe769d66d5cf86997bc741accd26f4cbc` |
| fresh-wheel bundle | manifest schema `deeplaw.fresh-wheel-bundle-manifest/v1`; bundle SHA-256 `ccc3b692d910b16c8311e07beef3c76c95ef0726b3fc0d38a58ae1569369d2d6` |
| installed runtime | isolated site-packages, version `0.12.0`, 287 contracts, Provider Capsule byte accounting valid |
| current Codex local plugin lifecycle | marketplace discovery/install/remove/re-add/cache-copy passed with `codex-cli 0.147.0-alpha.1.2`; no model call; `claim_eligible=false` |

The current local full suite reported `1581 passed, 6 skipped`. This is one Darwin environment,
not a three-OS qualification. The terminal CI run for the prior HEAD had green Linux/macOS
Python 3.11/3.12/3.13 lanes and three Windows failures caused by one platform-specific test
assertion. The assertion is corrected in the Pass 14 implementation candidate, but the new
current-HEAD CI result remains pending. Signing, provenance, public redownload and real Host
qualification remain `not_executed`; `release_ready=false` and `claim_eligible=false`.
