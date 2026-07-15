---
name: research-chinese-law
description: "Use only after the user explicitly invokes this skill to retrieve or verify Chinese legal sources, document numbers, exact articles, historical versions, effective dates, elements, legal issues, or citations. Do not invoke implicitly for ordinary coding, data analysis, document extraction, translation, project or case management, private evidence review, or isolated words such as 诈骗, 案件, 法务, fraud, or risk in filenames, columns, or prose."
---

# DeepLaw Chinese-Law Research

DeepLaw is a read-only Agent legal-knowledge substrate. Use its official catalog
to collect bounded, version-aware evidence. It may also read a physically
separate user-private legal-reference library, but those results are
user-provided and never official DeepLaw sources. Do not use either scope to
decide case facts, replace legal review, or inject a general legal corpus into
every conversation.

## Enforce the invocation gate

Proceed only when the user explicitly invokes this skill:

- Codex: `$research-chinese-law`
- Claude Code: `/deeplaw:research-chinese-law`
- OpenCode: an explicit `@deeplaw` request that loads `research-chinese-law`

If that gate is absent, continue the user's non-legal work without DeepLaw. Do
not infer legal intent from a project name, a case existing in the workspace, or
a lone domain word.

Even after explicit invocation, do not call DeepLaw for:

- application code, SQL, statistics, dashboards, or ordinary data work;
- OCR, PDF/DOCX extraction, summarization, rewriting, or translation alone;
- storing, searching, or summarizing case-private evidence or chat history;
- UI, session, SQLite, DuckDB, attachment, or project-management questions;
- a keyword that is only data, such as a `诈骗` category or `fraud_score` column.

## Use exactly one tool

Use the DeepLaw MCP tool whose leaf name is exactly `law_support`. Hosts may add
their own server or plugin prefix to that name. The prefix is not a second tool.

Do not use any other DeepLaw tool. If the server advertises a different leaf name
or more than one tool, stop and report an adapter/runtime contract mismatch.

`law_support` routes eight read-only operations:

- `search`: return a bounded evidence-card set;
- `get`: fetch one exact segment selected by `segment_id`;
- `verify`: verify one `segment_id` and `receipt_id` pair;
- `release_info`: inspect the active immutable release.
- `private_search`: search only the user-private legal-reference snapshot;
- `private_get`: fetch one exact private segment;
- `private_verify`: verify one private snapshot receipt;
- `private_info`: inspect the current private snapshot.

Use `private_*` only when the user explicitly asks to use their DeepLaw private
legal-reference library. Never infer private scope from a workspace, filename,
or case project. Do not combine official and private candidates into one ranking
or authority conclusion; query and label the two scopes separately when both
are explicitly requested.

Never ask for or invent an MCP corpus-write, memory-write, upload, delete,
reindex, or administration operation. Private add/delete and official updates
are out-of-band local CLI administration, not Agent tools.

## Minimize private facts before retrieval

DeepLaw is not case storage. Its private scope is for the user's legal reference
materials, not case evidence, facts, attachments, chats, or memory. Convert case
facts into the smallest abstract legal issue that can retrieve the rule. Remove names,
identity numbers, account numbers, phone numbers, addresses, filenames, internal
case IDs, quoted chats, and attachment contents. Do not send a whole case summary.

If de-identification would remove facts essential to the legal question, ask the
user to confirm a narrower neutral formulation. Never send direct identifiers or
persist private facts in DeepLaw.

## Retrieve in bounded stages

1. Identify the legal research purpose:
   - `exact_citation` for a named law, document number, or article;
   - `as_of_version` when the law on a specified date matters;
   - `elements` for statutory elements or constituent requirements;
   - `legal_issue_screen` for neutral issue spotting from abstracted facts;
   - `citation_verify` to check a proposed citation;
   - `broad_topic` only when the user explicitly requests a multi-rule topic
     synthesis. A lone term such as `诈骗` is navigation, not `broad_topic`.
2. Call `search`. Prefer `limit: 3`; never exceed `5`. Use the smallest useful
   `max_chars`, normally `2000`. Supply `as_of` in `YYYY-MM-DD` when the user asks
   about a historical or event-date rule.
3. Inspect evidence-card metadata before reading full text: title, document type,
   issuer, article label, status, effective interval, official source, hit reason,
   release ID, extraction method/warnings, temporal-review flag, and
   extraction-review flag.
4. Call `get` only for the one or two segments needed to answer. Do not fetch full
   text for every hit.
5. Call `verify` for each segment that will support a material citation. Do not
   verify unused candidates.
6. Call `release_info` or `private_info` only when corpus provenance is requested or release state
   itself is material. Do not add it as a routine token cost.

For an explicit one-word topic, keep `purpose: auto`, return a short navigation
answer from the primary rule, and offer narrower follow-up choices. Do not dump a
wide semantic top-k.

## Answer from evidence, not memory

- Separate retrieved normalized segment text from the agent's interpretation,
  and retain the official-source locator for comparison.
- For every `private_*` result, state that it is user-provided and unreviewed by
  the DeepLaw team. A `private://source/...` locator is an integrity binding, not
  an official download URL or authority claim.
- Identify the release, source title, document number when present, exact article
  or locator, status, effective interval, and official source URL.
- State when temporal applicability or authority relationships require human
  review. Matching a date does not prove that a rule applies to a case.
- State when the active release lacks a complete amendment/repeal lineage. An
  `as_of` filter over incomplete metadata is a bounded candidate search, not a
  guarantee that every historical version is present.
- For `extraction_review_required: true`, use the page locator and official
  source to request or perform source comparison before quoting material text.
- Treat notices, missing sources, ambiguous versions, failed receipts, and
  `temporal_review_required` as limitations, not details to hide.
- If DeepLaw is unavailable or has no verified source, say so. Do not silently
  substitute model memory, generated topic pages, discovery indexes, or web search.
- Never present the result as legal advice, a verdict, a finding of fact, or a
  complete authority set.

Keep the final answer compact unless the user explicitly requests a memorandum.
Quote only the minimum text needed and prefer precise locators over long excerpts.
