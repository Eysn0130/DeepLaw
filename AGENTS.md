# DeepLaw Agent Guide

DeepLaw is a read-only, version-aware Agent Knowledge Base for Chinese legal
sources, used by Codex, Claude Code, OpenCode, and future Analytix integration.

## Source Of Truth

- Runtime behavior is defined by `src/deeplaw`, tests, schemas, and the pinned
  dependency lockfile.
- Legal text is authoritative only when it belongs to an immutable release and
  retains its official source URL, source SHA-256, locator, and release ID.
- Generated topic pages, summaries, tags, graphs, embeddings, model output, and search
  rankings are derived data. They never replace source text or determine legal
  validity.
- GitHub mirrors and fixtures are not authoritative legal sources.

## Safety Boundaries

- Keep the MCP surface read-only. Do not add corpus, memory, or case write tools.
- Never mix case-private documents, facts, chats, or identifiers into a public
  DeepLaw release, cache, log, benchmark, or query corpus.
- Do not claim that a retrieved rule applies to a case merely because its
  effective date matches. Temporal applicability can require legal review.
- Do not silently fall back to model memory or web search when a release is
  missing or verification fails.
- Keep provider-visible output bounded. Search returns at most five evidence
  cards; full text is fetched by exact segment ID.
- Runtime database access is SQLite read-only and immutable.

## Engineering Discipline

- Prefer standard-library code and the smallest stable dependency set.
- Preserve document order, article boundaries, page/paragraph locators, and
  hashes through ingestion.
- Add or update tests for every contract change.
- Do not commit source DOCX/PDF files, generated release databases, credentials,
  private notes, or local paths containing user material.
- Use `uv run pytest`, `uv run ruff check .`, and `git diff --check` before
  handoff.

## Repository Layout

- `src/deeplaw`: ingestion, retrieval, audit, CLI, and MCP runtime.
- `contracts`: stable JSON contracts shared with hosts.
- `plugins/deeplaw`: Codex and Claude Code plugin package.
- `adapters`: host-specific thin configuration.
- `evals`: source-free retrieval evaluation cases.
- `docs`: architecture, governance, security, and integration decisions.
- `var`: local generated releases; never committed except `.gitkeep`.
