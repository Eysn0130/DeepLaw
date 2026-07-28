# Obsidian native vault workflow v1

Pinned application release: Obsidian Desktop `1.12.7`. Use a fresh local vault,
Restricted Mode, no community plugins, no Sync, no Publish, and no network
access during the measured task. Retain a screen recording and the exact vault
bytes before and after every task.

For each frozen case, the evaluator starts from the same source corpus and
performs the following scripted tasks without generated helper files:

1. copy the assigned source files into the vault and wait for local indexing;
2. find the exact source for the unmodified question using native Search;
3. open and record the supporting file and exact line/heading locator;
4. identify an explicitly superseded or stale note where the case requires it;
5. correct a seeded wrong note through the editor and record elapsed time;
6. inspect Local Graph for the requested relation;
7. copy a token-bounded context into the fixed reader Agent;
8. trace the reader answer back to the opened source.

Record task success, useful-context recall, irrelevant-context rate, source/span
coverage, stale leakage, context tokens, indexing time, query time and operator
time per case. A missing native capability is a retained failure, not a reason
to add a plugin after seeing results. The evaluator must record the exact
installed application build and operating-system build in the final commitment.
