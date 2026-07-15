# Security Policy

DeepLaw is an alpha, read-only Chinese legal-evidence retrieval project. The
repository distributes code under Apache License 2.0; it does not distribute
legal-source packages, case documents, generated release databases, or OCR
corpora.

## Supported versions

There is no stable public release yet. Security fixes are currently evaluated
against the `main` branch. Older commits, local candidate releases, and
third-party packages are not separately supported unless a future release says
otherwise.

## Report a vulnerability privately

Do not open a public issue, discussion, pull request, or social-media post with
vulnerability details.

Use the repository Security page and select **Report a vulnerability**:

<https://github.com/Eysn0130/DeepLaw/security/advisories/new>

If that control is not available, do not disclose the details publicly. Open
at most a detail-free issue asking the maintainer to enable a private reporting
channel, then wait for a private channel before sharing technical information.
Do not place an exploit, affected path, credential, private identifier, or
sensitive log in that public request.

Include privately, when safe:

- the affected commit or version;
- impact and the boundary crossed;
- a minimal reproduction using synthetic or source-free data;
- whether credentials, case-private data, legal-source text, or release
  artifacts may have been exposed;
- a suggested mitigation, if known.

Never send live credentials, real case materials, private user identifiers,
source DOCX/PDF files, generated SQLite releases, full OCR text, or unredacted
host logs. Describe sensitive material and provide the smallest synthetic
reproduction instead.

The project does not currently promise a response or remediation SLA. The
maintainer will coordinate disclosure and remediation on a best-effort basis.
Please keep the report private until a fix or an agreed disclosure date exists.

## Security scope

Examples of security-relevant reports include:

- a write path or command execution reachable through the read-only runtime;
- path traversal, symlink escape, or release-boundary bypass;
- receipt, hash, release pinning, or immutable-database verification bypass;
- leakage of credentials, case-private data, host paths, or provider-visible
  data beyond the documented budget;
- unsafe archive, parser, MCP, or dependency behavior with a concrete impact.

Legal interpretation disagreements, source-currentness corrections, retrieval
quality suggestions, and documentation errors are normally not security
vulnerabilities. They may be reported through a public issue only when the
report contains no private, licensed, or otherwise restricted material.

## Authorization boundary

This policy does not authorize access to systems, accounts, data, or legal
sources that you do not own or have permission to test. Test only with
synthetic data or material you are authorized to use.
