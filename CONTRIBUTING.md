# Contributing to DeepLaw

Thank you for helping improve DeepLaw. Keep contributions focused, reviewable,
and compatible with its read-only legal-evidence boundary.

## Before opening an issue or pull request

- Search existing issues before creating a duplicate.
- Use a public issue only for information that is safe to publish.
- Report vulnerabilities privately as described in [SECURITY.md](SECURITY.md).
- Discuss changes to public schemas, MCP behavior, release identity,
  persistence, or corpus governance before implementing them.
- Do not use this repository to request legal advice, case conclusions, or a
  determination that a rule applies to particular facts.

## Development setup

DeepLaw requires Python 3.11 or newer and `uv`.

```bash
uv sync --extra dev
uv run deeplaw --version
```

Run the smallest focused test while working, then run the repository checks
before submitting:

```bash
uv lock --check
uv run ruff check .
uv run pytest
git diff --check
```

CI executes the lock check, Ruff, and pytest on Python 3.11 and 3.13. The
repository does not currently contain a portable Codex, Claude Code, or OpenCode
plugin validator, so CI does not claim to run one.

## Change expectations

- Make the smallest complete change and preserve unrelated code and docs.
- Add or update tests for behavior and contract changes.
- Update every affected JSON schema and consumer when a public contract
  changes.
- Keep the MCP surface read-only. Do not add corpus, memory, case, or generic
  filesystem write tools.
- Keep examples and fixtures source-free or synthetic.
- Update `uv.lock` when dependency metadata changes; do not refresh it for an
  unrelated change.
- Document third-party code or assets in `THIRD_PARTY_NOTICES.md` when their
  license or attribution requires it.

## Data and legal-source boundary

Do not commit or attach:

- case-private documents, facts, chats, identifiers, or analysis;
- API keys, tokens, cookies, credentials, private keys, or secret-bearing
  configuration;
- source DOCX/PDF files, OCR output, full legal text, or private mirrors;
- generated SQLite releases, embeddings, indexes, caches, or local audit
  artifacts;
- logs or screenshots that expose sensitive local paths or host data.

DeepLaw source code is licensed under Apache License 2.0. The repository license
does not grant rights to legal-source material obtained separately by an
operator, third-party trademarks, or third-party assets. Contributors are
responsible for having the right to submit every included file.

## Pull requests

A pull request should explain:

1. the problem and intended behavior;
2. affected contracts and risk boundaries;
3. tests or other evidence that prove the change;
4. any privacy, source, or licensing impact.

By submitting a contribution, you agree that your contribution is licensed
under Apache License 2.0 and that you have the necessary rights to provide it.
No contribution may include legal-source or case material merely because it is
available to the contributor locally.
