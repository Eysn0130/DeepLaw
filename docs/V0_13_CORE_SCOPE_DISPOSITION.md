# DeepLaw v0.13 Core Scope Freeze disposition

Decision: **continue, but contract the scope; do not release**.

Implementation freeze: commit `450e79e66a30399385ab4afd2d137414e78b7119`, tree
`c368e2ccbb45ae1e641e29d74e458b587c6fe6ba`.

The Core Scope Freeze does not support a stop-and-abandon decision: one current Task Checkpoint is
now recoverable through the recommended v6 Context seam, and the bounded Evidence Wiki task
exercises the intended evidence/Authority chain. It also does not support expansion or release:
continuity missed one frozen context-efficiency threshold, and the frozen legal task admitted no
current primary evidence from its unsigned development Pack.

## Core outcome status

| Outcome | Disposition | Evidence |
| --- | --- | --- |
| Cross-thread continuity | **partial / continue** | Correct current action, decisions, gaps, no stale/distractor admission, bounded Provider payload; `RelevantChars / ContextChars` failed at `0.760628 < 0.8`; no real Host or independent Human Gold |
| Evidence Wiki | **development pass / continue narrowly** | Exact bytes/Revision/fragment/locator/Statement/Relation/Ledger/Registry/Link Index/Wiki/read chain passed; Statement resolver remains deferred; no independent human usability run |
| Legal exact evidence | **failed / stop qualification claim** | Future version excluded and hard-zero Authority/citation/version failures stayed zero, but current and exception primary evidence were absent because the development Pack was unsigned and temporally unverified |
| Duplicate retrieval surfaces | **contract documentation now, migration later** | `knowledge context` is the only recommended Agent seam; `knowledge query` is operator-only; legacy recalls are retained pending consumer inventory |

## Allowed continuation

Only the following work is justified before reopening product scope:

1. obtain an independently reviewed repository-external continuity Gold and a fresh unseen
   multi-case holdout; then execute isolated real Codex runs through `knowledge context`;
2. run a real human Wiki task against the existing page/receipt/link/source chain, without adding
   Guides, Relation Path, new page families, or a GUI;
3. obtain a signed or equivalently verified legal development Pack plus independent legal Gold;
4. after those three pass, decide whether the current candidate merits scale, 3-OS, wheel,
   SBOM/provenance, and public-redownload qualification.

The observed development sources are no longer blind. Any repair based on their results requires a
new unseen holdout for qualification.

## Reproduction commands

The repository commands are exact. Repository-external locations are intentionally represented by
role placeholders so no private local path is persisted:

```bash
uv run --frozen python benchmarks/v013/continuity_candidate.py host-only \
  --thread-b-source <thread-b-source> --output <host-only-candidate>
uv run --frozen python benchmarks/v013/continuity_candidate.py host-plus-deeplaw \
  --thread-a-source <thread-a-source> --thread-b-source <thread-b-source> \
  --output <host-plus-candidate>
uv run --frozen python benchmarks/v013/score_continuity.py \
  <host-only-candidate> <host-plus-candidate> <continuity-gold> --output <continuity-score>

uv run --frozen python benchmarks/v013/evidence_wiki_candidate.py \
  <wiki-source> --output <wiki-candidate>
uv run --frozen python benchmarks/v013/score_evidence_wiki.py \
  <wiki-candidate> <wiki-gold> --output <wiki-score>

uv run --frozen python benchmarks/v013/legal_exact_evidence_candidate.py \
  <legal-source> --output <legal-candidate>
uv run --frozen python benchmarks/v013/score_legal_exact_evidence.py \
  <legal-gold> <legal-candidate> --output <legal-score>

uv lock --check
uv run --frozen pytest --strict-markers
uv run --frozen ruff check .
git diff --check
```

The final candidate processes were additionally wrapped in a local deny-network sandbox that
denied Gold and scorer reads. Each scorer was separately denied the source and candidate
implementation. These profiles are environment policy, not a repository credential or product
contract.

## Not executed and blocked

- independent continuity Human Gold and independent legal Human Gold;
- real Codex x3;
- OpenCode/DeepSeek x3: `blocked_not_executed` until the Owner revokes the old key and supplies a
  new owner-only evaluation secret outside the repository;
- exact signed 28-source legal Pack;
- current-candidate 10k/100k and 10,000-request RSS work;
- Linux/Windows and the full Python 3.11/3.12/3.13 matrix;
- fresh wheel/sdist, SBOM, provenance, reproducible artifacts, and public redownload.

No package version, tag, RC, GA, catalog, or release artifact is created.

`release_gate_passed=false`

`claim_eligible=false`

`competitive_claim_eligible=false`

`package_version=0.12.0`
