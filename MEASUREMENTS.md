# Measurements

Every gate number this project has produced, in one place. Phase 07 compares
against all of them, and Phase 03 needs its baseline visible daily.

Append, never edit. A number that moved is two rows, not one row changed.

---

## Phase 00 — ground truth

Pinned SHA `99bd9d1d46` · scope `packages/vite/src/` · `SINCE_YEARS=6`

| | |
|---|---|
| closed issues fetched | 8,326 |
| **usable labeled examples** | **1,375** (16.5%) |
| train / val / test | 962 / 206 / 207 |
| train range | 2020-04-24 → 2023-07-15 |
| val range | 2023-07-20 → 2024-11-04 |
| test range (sealed until Phase 07) | 2024-11-13 → 2026-08-24 |
| mean gold files per example | 1.68 |
| single-file fixes | 902 (66%) |
| `mentions_gold_path` (leakage flag) | 256 (18.6%) |

Dominant funnel loss: **75.5% of closed issues have no traceable fix** —
duplicates, stale-bot closures, environment problems. Expected, not a bug.

Corpus to be retrieved over: **141 source files, ~426k tokens.**
Issue text: median 686 tokens, mean 1,019, p90 ~1,850.

Contamination note: 169 of 207 test issues were filed in 2025–2026, which
postdates the training data of the models being evaluated. The temporal split
was chosen to avoid training on the future; it also happens to be the best
available defense against memorized fixes.

---

## Phase 01 — naive baseline, no retrieval, no tools

Same issue (#13143, full body) through both providers:

| | qwen2.5-coder:7b (local) | claude-haiku-4-5 |
|---|---|---|
| input tokens | 679 | 773 |
| output tokens | 168 | 443 |
| latency | 12.8s | 5.8s |
| **cost per triage** | **$0.00** | **$0.002988** |

Where the input tokens go: ~96% is the issue body, against a ~25-token system
prompt. So there is almost nothing cacheable at this phase, and output is ~73%
of the dollar cost (5x the input rate). **Both facts invert in Phase 03**, when
retrieval starts prepending thousands of tokens of code — that is when caching
starts paying, which is why its gate sits in Phase 07.

Quality note, unscored: Haiku named "Vite's Less plugin", which maps to the gold
label `packages/vite/src/node/plugins/css.ts`. Qwen identified the bug correctly
but named no subsystem. First signal on the local-vs-paid tradeoff.

Embedding throughput (Phase 03 prerequisite, measured): `nomic-embed-text`,
768 dimensions, ~7 chunks/s → full corpus indexes in **~166s, free, locally**.

---

## Does the pipeline generalize? (measured 2026-10-18)

The same miner, unchanged, pointed at a second corpus: a private Swift repo of
my own, 11 days old, merge-commit workflow rather than squash, no monorepo
`packages/` layout. Its config lives in a gitignored `.env.bowling`, and its
mined data stays out of this public repo.

| | vitejs/vite | private Swift repo |
|---|---|---|
| language | TypeScript | Swift |
| merge style | squash, `...(#N)` | merge commit, `Merge pull request #N` |
| closed issues | 8,326 | 21 |
| dropped: no linked fix | **75.5%** | **4.8%** |
| **issues → usable labels** | **16.5%** | **76.2%** |
| usable examples | 1,375 | 16 |
| mean gold files | 1.68 | 2.62 |
| issue names a gold file | 18.6% | **0%** |

Two findings worth keeping:

1. **The pipeline is not Vite-specific.** A different language, merge style and
   layout needed no code change — only `.env` values, once `SOURCE_EXTS`,
   `TEST_MARKERS` and `DATA_DIR` were made env-driven (they had been hardcoded,
   so the README's "re-pointing is an .env edit" was previously only true for
   another TypeScript repo).

2. **Label density is a property of maintainer discipline, not repo size.** A
   solo repo where every PR closes an issue converts 4.6x better than Vite.
   Vite's 75.5% "no linked fix" loss is duplicates, stale-bot closures and
   environment reports — the cost of a popular public tracker, not of the
   mining approach. The Swift repo also has zero leakage: not one issue names the file
   that was changed, making its 16 examples cleaner (and harder) than Vite's.

It cannot serve as the eval corpus — recall@10 over 16 rows moves in 6%
steps, so it cannot separate a real improvement from noise. Its uses are:
a demo on a codebase whose answers can be judged by hand, and a **held-out
generalization check in Phase 07** — run the Vite-tuned retriever against it
unchanged and see whether recall survives a language change.

---

## Phase 02 — typed output

Classification scored against the GitHub labels the miner already captured, on a
**fixed 40-row slice of val** (lowest issue numbers first, so runs are
comparable). `python -m eval.classify --n 40`.

| | qwen2.5-coder:7b | claude-haiku-5-5 |
|---|---|---|
| severity | 24/40 — **60%** | 26/40 — **65%** |
| majority-class baseline | 52% | 52% |
| component | 8/15 — 53% | 14/15 — **93%** |
| contract violations | — | 0/40 |
| wall clock | 784s | 157s |
| cost | $0.00 | $0.0286 |

**Always quote severity against the 52% baseline.** A constant `"medium"`
predictor scores 52% on this slice, and an earlier prompt revision scored
*higher* than its predecessor purely by collapsing to that one class. The
prediction spread and the confusion matrix are what distinguish skill from
degeneracy, so report them with the number.

**Severity is near a ceiling set by the label, not the prompt.** Three prompt
revisions (impact framing, priority framing, tie-break removal) all land within
a few points of baseline, and nearly every error is off by exactly one tier in
both directions. Vite's `p2`/`p3`/`p4` encode maintainer roadmap context — what
is already queued, how risky a fix is near a release — which is absent from the
issue text. 65% against 52% with that caveat is the honest result; tuning it
further would be fitting noise.

**Components is the real signal, and the headline number.** 93% against 53% is a
40-point gap between a 7B and Haiku on the one axis that genuinely is present in
the report: a less `@import` bug names less and `@import`, and concluding `css`
is inference from evidence. It is also the direct precursor to Phase 03 —
`feat: css` → `packages/vite/src/node/plugins/css.ts` is the same localisation
gradient at finer resolution.

### Two findings that shaped the design

**Field order is generation order.** Grammar-constrained decoding emits
properties in schema order, so a trailing `reasoning` field rationalises a
verdict already committed to. Moving it first — one line — is what lifted
severity off the baseline and broke the single-class collapse. The highest-
leverage change of the phase, and its mechanism was verifiable in advance.

**Constrained decoding is not validation.** Both providers guarantee object
shape, property names, enums and types, and neither enforces value constraints.
Measured three times: Ollama returned `confidence: 90` against `maximum: 1`;
Haiku wrote 1,340 chars against `maxLength: 1000`, then 7 items against
`maxItems: 6`. Pydantic at the boundary is load-bearing, not belt-and-braces,
and is precisely what lets the frontend render with zero defensive field access.

### Cost note

Claude Haiku 5.5 ($0.10 / $0.50 per MTok) is 10x cheaper than Haiku 4.5 and is
now the `ANTHROPIC_MODEL` default. Per structured triage: ~$0.0007. A full
206-row val run is ~15 cents. Paid runs are no longer worth rationing.

---

## Phase 03 — retrieval

Scored on **all 206 val rows**. `python -m eval.retrieve --split val --n 206
--strategy fixed_v1`. No model calls, so every number here is free.

Definitions, because subtly different ones are not comparable:
`recall@k` is per-issue *fraction of that issue's gold files* in the top k
retrieved files, averaged over issues — not hit/miss, since mean gold count is
1.68. `mrr` is 1/(rank of the first gold file found). A hit is a **file**, so
this is file-level localisation; `css.ts` is 3,685 lines and counting it as found
does not mean the bug was pinpointed. `clean` excludes the 31 rows whose issue
text names a gold file — finding those is string matching, not retrieval, and
clean is the number to quote.

### v1 baseline — fixed character windows (THE NUMBER TO BEAT)

141 files in scope → **1,541 chunks**, 1,200 chars with 200 overlap, embedded
with `nomic-embed-text` (768d) in 63s. Query is the raw issue title + body,
never model-rewritten. 206 issues scored in 35s.

| | all | clean |
|---|---|---|
| recall@1 | 16.7% | **16.0%** |
| recall@5 | 51.6% | **49.7%** |
| **recall@10** | 65.9% | **64.1%** |
| MRR | 36.1% | **34.5%** |

**recall@10 = 64.1% clean is the v1 baseline.** Every later retrieval claim is
measured against it.

Three things this says before any improvement is attempted:

1. **The naive version is stronger than it deserves to be.** Fixed windows carry
   no file path, no imports and no syntax awareness, yet a gold file reaches the
   top 10 of 141 about two thirds of the time. Vite's filenames and identifiers
   are semantically loaded, so even a mangled window from `css.ts` says "css"
   repeatedly. Expect v2's headroom on recall@10 to be smaller than intuition
   suggests.
2. **The headroom is at the top of the ranking, not in coverage.** recall@1 is
   16.0% and MRR 34.5%, so the first correct file averages around rank 3. The
   right file is usually *somewhere* in the list; it is rarely first. Precision
   at rank 1-3 is what v2 has to buy.
3. **Leakage is not inflating the headline** — 65.9% all vs 64.1% clean, a 1.8
   point gap across 31 rows. The dataset's `mentions_gold_path` flag was worth
   building, and it turns out not to matter much here.

### v1 + header — the same windows, told where they came from

`p3d` bundles two independent changes: splitting on syntax, and giving a chunk
context about itself. Separated them and tested the cheap one first. Every chunk
gets the file path and up to 20 import lines prepended; chunk boundaries are
byte-for-byte identical to v1. Three lines of code, no new dependency.

| | v1 clean | v1+header clean | change |
|---|---|---|---|
| **recall@1** | 16.0% | **24.1%** | **+8.1 pts, +51% relative** |
| recall@5 | 49.7% | 54.0% | +4.3 |
| recall@10 | 64.1% | **68.5%** | +4.4 |
| **MRR** | 34.5% | **43.8%** | **+9.3 pts, +27% relative** |

Indexing 63s → 98s (more tokens to embed). Scoring unchanged at ~37s. Free.

**The mechanism.** Before, a chunk of `css.ts` was retrievable only if that
particular 1,200-character window happened to contain the issue's vocabulary —
"less", "@import", "postcss". Most windows of a 3,685-line file do not, so a
file's rank was decided by whether the bug's words fell inside one lucky window.
With the header, all 14 chunks of `css.ts` carry
`packages/vite/src/node/plugins/css.ts` and `import type Less from 'less'`, so
the whole file is reachable from a topical query. Files rank by their single best
chunk, so raising the floor across every chunk of a file raises where that file
lands — which is rank precision, not coverage.

**The methodological point, and the one worth saying out loud.** recall@10 moved
only 4.4 points. Judged by the headline metric alone this change looks marginal;
it actually improved rank-1 accuracy by half. The baseline had already shown that
the headroom was at the top of the ranking (recall@1 16%, MRR 34.5%, first hit
around rank 3), which is why recall@1 and MRR were the metrics to watch. Picking
the metric *before* running the experiment is what made this legible.

### v2 — tree-sitter function boundaries (a negative result, with a cause)

One chunk per top-level declaration via tree-sitter, header kept identical so
only the boundaries changed. Oversized units sub-windowed; large classes split
into methods; unparseable files fall back to v1+header.

| | v1 clean | v1+header clean | function_v2 clean |
|---|---|---|---|
| recall@1 | 16.0% | **24.1%** | 22.7% |
| recall@5 | 49.7% | **54.0%** | 53.5% |
| recall@10 | 64.1% | **68.5%** | 68.1% |
| MRR | 34.5% | **43.8%** | 43.0% |

1,541 → 2,440 chunks; indexing 98s → 141s. **Slightly worse on every metric**,
for more chunks and more time.

**The cause, measured rather than guessed:**

| | median body chars | chunks under 200 chars |
|---|---|---|
| v1+header | 1,200 | 0% (1 of 1,541) |
| function_v2 | 344 | **39% (958 of 2,440)** |

Respecting syntax means respecting that source files are mostly one-line
declarations. `const decoder = new TextDecoder()` is 33 characters against a
~250-character header, so 39% of v2's chunks are **majority header** — their
embeddings are dominated by the path and import list, making every small chunk
in a file near-identical to every other. The index gained ~900 low-signal
near-duplicate vectors competing for top-k slots, and because files rank by
their single best chunk, that best chunk became more often a header-dominated
fragment than real code.

**What this says about the project, not just the chunker.** The header result and
this one point the same way: in this corpus the retrievable signal lives at
*file* granularity — what a file is about — rather than at declaration
granularity. Issue text says "less @import fails"; it does not name a function.
So context beat boundaries, and finer boundaries actively hurt once chunks fell
below the size where they carry meaning.

**The implied fix, untested:** merge adjacent small declarations until each chunk
carries a floor of real code (~600-800 chars), keeping syntax boundaries only
where they fall at useful sizes. That is the version that would beat v1+header,
and the hypothesis is specific enough to be falsifiable.

### Generalization — the Vite-tuned retriever on the private Swift repo (2026-10-27)

`fixed_v1_header`, unchanged from Vite, on all 16 rows. The corpus is under
split.py's 30-row floor, so `--split all` scores the whole dataset; it refuses
on any corpus that has a sealed test split.
`ENV_FILE=.env.bowling python -m eval.retrieve --split all --strategy fixed_v1_header`

| | vitejs/vite val, clean (n=175) | private Swift repo (n=16, 0 leaky) |
|---|---|---|
| recall@1 | 24.1% | 44.4% |
| recall@5 | 54.0% | 73.2% |
| recall@10 | 68.5% | **78.0%** |
| MRR | 43.8% | **87.5%** |
| recall@1 ceiling (mean of 1/gold count) | 86.3% | 54.3% |
| files in scope | 141 | 58 |

**What it says:** recall survived a change of language with no Swift grammar
installed, because v1+header needs none. The header carries the signal, not
the parser. MRR of 87.5% means the first right file is usually at rank 1.
recall@1 looks lower only because these fixes touch 2.6 files on average, which
caps it at 54.3%.

**What it does not say:** that the retriever is better on Swift. There are 2.4x
fewer files to choose among, and the issues name intentions rather than
symptoms, and intentions match file names. With 16 rows each one is worth
~6 points. Read it as "did not collapse", not as a comparison.

**Refreshed the same day at a newer pin (172 commits later).** 37 closed
issues → 28 usable (75.7%), 120 in-scope files, 383 chunks, mean 3.68 gold
files. recall@1 38.7% (ceiling 43.7%), recall@5 63.3%, **recall@10 71.2%,
MRR 92.3%**. Recall fell as the fixes grew wider (2.6 → 3.7 files each) and
the repo doubled. MRR rose: the first right file is almost always at rank 1.

**On its 25 open issues, judged by hand.** Issues that describe code put the
obvious file first, often at the right lines. Issues that describe process,
like a beta test, a licensing conversation or a done-criteria review, still get
five files, because the retriever has no way to abstain. Those had the lowest
top scores (0.64–0.72, against ~0.75–0.85 for code issues), so a score floor
is a cheap first abstain signal. It is untested, and the ranges overlap.

---

## Grounded triage — the model re-ranks retrieval's top 10 (2026-10-27)

`/triage/grounded`: retrieval (`fixed_v1_header`) finds 10 candidate files,
and the structured-triage model gets the issue plus each candidate's path and
best chunk. It names up to 5 of them in `suspected_files`, most likely first,
from the list only. Both rankings are scored with the same `score_ranking`,
from the same candidates, on the fixed 40-row val slice.
`python -m eval.grounded --n 40`.

The model only sees the top 10, so retrieval's recall@10 is its ceiling.
recall@1 and MRR were picked in advance as the metrics it could move.

| clean (n=33) | retrieval | claude-haiku-5-5 | qwen2.5-coder:7b |
|---|---|---|---|
| **recall@1** | 14.6% | **33.4%** | 14.6% |
| recall@5 | 49.6% | 49.6% | 26.8% |
| **MRR** | 34.9% | **50.5%** | 24.2% |
| ceiling (retrieval recall@10) | 60.9% | | |
| contract violations | — | 0/40 | 9/40 |
| rows naming no file | — | 0 | 14 (9 of them violations) |
| wall clock | ~7s | 293s | 2,517s |
| cost, 40 rows | $0 | $0.0704 (~$0.0018 each) | $0 |

(The same slice on all rows, n=40: retrieval / Haiku / qwen recall@1
19.9% / 37.9% / 19.9%, MRR 40.6% / 55.4% / 30.8%.)

**Haiku more than doubles recall@1 and leaves recall@5 untouched.** The
model doesn't find different files: the right file was already somewhere in
the top 5, and the model moves it to first. That is exactly the one gain this
design allowed. At ~$0.0018 a triage, re-ranking is the cheapest
rank-precision gain measured so far: the header bought +8 points of
recall@1, this buys +19 on this slice.

**qwen2.5-coder:7b does not re-rank.** Its recall@1 equals retrieval's
exactly, which looks like it keeps the search order. It names fewer files
(14 rows with none), and 9 of 40 rows named a path that wasn't among the
candidates. That is a contract violation, scored zero, and a 500 at the
route. The 40-point Phase 02 component gap between the two models shows up
here as the difference between re-ranking and not.

**Caveats.** 40 rows, 33 clean: one row is worth ~3 points, so +19 is a large
effect on a small sample rather than a precise number. A full 206-row val
run (~$0.36) would firm it up. File-level, as everywhere in Phase 03.

**The trap this run nearly fell into.** A grounded prompt is ~6.9k tokens.
Ollama's default context window truncates a longer prompt *from the front*,
silently. On the first val row it read 2,050 of 6,897 tokens and lost the
system prompt, so qwen would have scored badly for a reason that had nothing
to do with qwen. `OLLAMA_NUM_CTX` (default 16384) is now passed on every
local call, and the numbers above are with the full prompt.

---

## Grounded triage on the full val split (2026-10-27)

`PROVIDER=anthropic python -m eval.grounded --n 206`. The same design as the
40-row run above: retrieval's top 10, the model names up to 5, both scored with
`score_ranking`. This is every val row, not a slice.

| clean (n=175) | retrieval | claude-haiku-5-5 |
|---|---|---|
| **recall@1** | 24.1% | **44.5%** |
| recall@5 | 54.0% | 58.8% |
| **MRR** | 43.8% | **59.5%** |
| ceiling (retrieval recall@10) | 68.5% | |

All rows (n=206): recall@1 26.1% → 47.5%, MRR 46.4% → 62.8%. Contract
violations 3/206. Cost $0.3603. Wall clock 1,418s. Files named per row,
`{0: 6, 1: 48, 2: 60, 3: 77, 4: 5, 5: 10}`.

**The 40-row result holds, and strengthens on the full split.** recall@1 gains
+20.4 points, against +19 on the slice. recall@5 also rises about 5 points,
which the 40-row run could not show. So on the full set Haiku also pulls gold
files up from ranks 6–10, not only reordering the top 5. This is the baseline
Phase 04 must beat.

---

## Phase 04 — tools (2026-10-27)

`PROVIDER=anthropic python -m eval.agent --n 40`. The same 40-row val slice as
the grounded run. Per row: retrieval's top 10, one grounded call that re-ranks
them, and the tool-use agent (grep, read_file, list_dir, git_log; the model may
name a file outside the candidates once a tool confirmed it). All three scored
by `score_ranking`.

| clean (n=33) | retrieval | grounded | agent |
|---|---|---|---|
| **recall@1** | 14.6% | 30.4% | **41.5%** |
| recall@5 | 49.6% | 49.6% | 55.2% |
| **MRR** | 34.9% | 49.5% | **54.5%** |
| ceiling (retrieval recall@10) | 60.9% | | |
| contract violations | — | 0/40 | 0/40 |
| gold found outside candidates | — | impossible | 3 rows, 3 files |
| turns per run | — | 1 | `{1: 26, 2: 10, 3: 1, 4: 1, 5: 1, 6: 1}` |
| tool calls | — | — | grep 22, read_file 5, list_dir 0, git_log 0 (0 errors) |
| wall clock | | | 659s for the whole run |
| cost per triage | $0 | $0.0017 | $0.0029 |
| cost, 40 rows | $0 | $0.0683 | $0.1146 |

All rows (n=40): recall@1 19.9% / 35.4% / 44.6%, recall@5 54.4% / 56.9% /
61.1%, MRR 40.6% / 55.4% / 59.6%, for retrieval / grounded / agent. The ceiling
over all rows is 66.5%.

Gold files found outside the candidates: #14009 `cli.ts`, #14151
`optimizer/scan.ts`, #14537 `plugins/esbuild.ts`.

**The agent beats grounded on recall@1 by 11 points and on MRR by 5, at 1.7x
the cost.** The spend is small in absolute terms ($0.0029 a triage). But 26 of
40 runs finished in one turn with no tool call at all, so some unknown share of
the lift comes from the agent prompt and request shape rather than from tools,
and this run does not separate tool rows from no-tool rows. Only 14 rows used
tools, with 27 calls in total, 22 of them grep and 5 read_file; list_dir and
git_log were never called. Scoring tool-using and no-tool rows separately would
separate the effects; a follow-up can do that.

**The new ability is real but small.** The ceiling that bound grounded triage,
retrieval's recall@10, is no longer a hard limit: in 3 rows the agent named a
gold file that was not among the 10 candidates, which grounded triage cannot do
by construction. That is 3 of 40 rows (all rows), so at most ~7 points of
all-row recall@5 headroom; it is not comparable to the clean-row recall@5 gain
over grounded (49.6% to 55.2%), which comes from a different row set and may
include re-ranking as well as these finds.

**No contract violations on either side.** Every agent run ended in a valid
`GroundedTriageResult` with in-scope paths. The turn limit was never hit
(max 6 turns).

**Caveats.** 40 rows, 33 clean: one row is ~3 points, so the gap between agent
and grounded (about 3 to 4 rows on clean) is a suggestive size on a small
sample, not a precise number. The grounded column here differs from the earlier 40-row run on the
same slice and prompt: clean recall@1 33.4% then, 30.4% now (3 points, one
row, more than a quarter of the 11-point agent-over-grounded gap), and clean MRR
50.5% then, 49.5% now. That is the observed difference between two runs, not a bound on
noise. The tools read code
as of the pin, after every val fix, as the retrieval index always has: a file
the agent finds by grep may exist only because the fix for the issue landed, so
"found outside" is optimistic by an amount this run cannot separate. The
`cutoff` argument limits git_log, which was never called. File-level, as
everywhere in Phase 03.
