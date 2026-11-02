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
them, and the tool-use agent (grep, read_file, list_dir, git_log). The prompt
tells the model it may name a file outside the candidates once a tool confirmed
it; that is an instruction, not an enforced rule (the code checks only that the
path exists and passes the scope test). All three scored by `score_ranking`.

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
`optimizer/scan.ts`, #14537 `plugins/esbuild.ts`. The eval did not record
per-row tool use, so it cannot say whether each of these three came from a
tool-using run. One later `agent.explain` run of #14009 did reach `cli.ts` via
grep and read_file, and another run of it missed: the agent is nondeterministic
per run.

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

## Phase 05a — does looking pay? (40 rows × 3 repeats)

Four arms ran on the same 40 val rows, three repeats each, with the same 10
retrieved candidates per issue (retrieval runs once per issue): `grounded`,
`agent-no-tools` (agent prompt, no tools), `agent` (agent prompt plus the four
tools), `agent-look` (agent plus the look-first nudge). 480 records, $1.1772
in total, 2 run errors, 0 api errors. Everything below is the output of
`python -m eval.ablation --report data/runs/val-n40-r3.jsonl`.

**Scores** (clean, n=99 runs = 33 issues × 3 repeats; mean over repeats, with
the min-max of the per-repeat scores):

| arm | recall@1 | recall@5 | MRR | flip rate |
|---|---|---|---|---|
| grounded | 0.334 (0.304-0.365) | 0.486 (0.451-0.541) | 0.502 (0.495-0.510) | 0.061 |
| agent-no-tools | 0.339 (0.304-0.380) | 0.445 (0.405-0.466) | 0.492 (0.465-0.530) | 0.091 |
| agent | 0.434 (0.395-0.456) | 0.575 (0.567-0.587) | 0.598 (0.563-0.616) | 0.212 |
| agent-look | 0.395 (0.395-0.395) | 0.590 (0.572-0.612) | 0.574 (0.566-0.581) | 0.061 |

On all 40 rows (n=120 runs) recall@1 is 0.386 / 0.375 / 0.478 / 0.429 in the
same order; recall@5 and MRR are in the report output kept with the run.

**Paired comparisons** (clean, recall@1, 33 issues; each arm's mean over its
repeats per issue; difference is the second arm minus the first):

| comparison | wins | losses | ties | diff | 95% interval | excludes 0 |
|---|---|---|---|---|---|---|
| grounded → agent-no-tools | 2 | 1 | 30 | +0.005 | [-0.020, +0.035] | no |
| agent-no-tools → agent | 7 | 0 | 26 | +0.094 | [+0.030, +0.175] | yes |
| agent → agent-look | 1 | 6 | 26 | -0.039 | [-0.084, +0.005] | no |
| grounded vs agent | 9 | 1 | 23 | +0.099 | [+0.027, +0.187] | yes |
| grounded vs agent-look | 4 | 2 | 27 | +0.061 | [-0.020, +0.162] | no |

The same chain on all 40 rows (40 issues, recall@1):

| comparison | wins | losses | ties | diff | 95% interval | excludes 0 |
|---|---|---|---|---|---|---|
| grounded → agent-no-tools | 3 | 3 | 34 | -0.011 | [-0.046, +0.021] | no |
| agent-no-tools → agent | 9 | 0 | 31 | +0.103 | [+0.039, +0.178] | yes |
| agent → agent-look | 1 | 8 | 31 | -0.049 | [-0.090, -0.008] | yes |
| grounded vs agent | 11 | 1 | 28 | +0.091 | [+0.028, +0.164] | yes |
| grounded vs agent-look | 5 | 3 | 32 | +0.043 | [-0.025, +0.125] | no |

Clean is the primary subset and the one quoted throughout, because the "all"
subset includes rows whose issue text names the gold file, and finding those
is string matching, not triage. Both subsets are shown where they differ in
verdict.

**Method.** The interval is a bootstrap over issues: 10,000 resamples, seed 0,
95% interval by linear-interpolation percentiles (numpy's default). A
difference counts only if its interval excludes zero; an interval touching
exactly 0 does not count. The Scores table weights each repeat equally (mean of
per-repeat means) while the Paired table weights each issue equally (repeats
averaged per issue), so the two can differ when an issue has fewer repeats than
others (api errors are excluded); here they coincide.

**Flip rate.** The share of issues whose rank-1 hit differs across repeats:
0.061 grounded, 0.091 agent-no-tools, 0.212 agent, 0.061 agent-look (clean).
The `agent` arm is the least stable: one issue in five changes its rank-1 hit
between repeats. One hypothesis is that tool use adds a branch (look or not
look) that the model takes differently from run to run, but the data partly
contradict it: `agent-look` used tools more (53% of runs against 33%) and has a
flip rate of 0.061 against `agent`'s 0.212. The flip rate here is not
explained by how often tools are used. `agent-look` is as stable as `grounded`, and its
three repeats scored exactly 0.395 each, which is a coincidence of the counts,
not zero variance (its clean flip rate is 0.061).

**Tool behaviour.**

| arm | used any tool | turns mean/p50/max | calls | tool error rate | picks seen |
|---|---|---|---|---|---|
| agent | 0.33 | 1.5/1/6 | grep=53, read_file=18 | 0.01 | 0.19 |
| agent-look | 0.53 | 1.9/2/6 | git_log=1, grep=78, list_dir=2, read_file=45 | 0.01 | 0.32 |

"Picks seen" is the share of picked files that appear in some tool output
during the run. It is descriptive only, not causal.

**Cost.**

| arm | $/triage | $/correct | p50 ms | p95 ms |
|---|---|---|---|---|
| grounded | 0.0018 | 0.0037 | 5990 | 8671 |
| agent-no-tools | 0.0018 | 0.0039 | 6600 | 8546 |
| agent | 0.0028 | 0.0048 | 6944 | 12335 |
| agent-look | 0.0034 | 0.0065 | 7632 | 11977 |

Failed runs are counted as zero hits and as paid. Run errors: 1 `grounded` (#14690 repeat 1) and 1 `agent-look` (#14065 repeat 1), both "final answer rejected: suspected_files not in scope at the pinned tree".

**Decision rule.** The rule was fixed before the run: confirm the agent arm
with the highest clean recall@1 whose paired interval against `grounded`
excludes zero; if two qualify with equal clean recall@1, the first in arm order
(agent-no-tools, agent, agent-look) wins, the simpler arm. Only `agent`
qualifies (+0.099, [+0.027, +0.187]), so the chosen arm is **agent**. No tie
arose.

**Finding 1: the tools, not the prompt, produce the gain.** Moving from
`grounded` to `agent-no-tools` changes only the system prompt and moves clean
recall@1 by +0.005 with an interval that includes zero (2 wins, 1 loss, 30
ties). Adding the tools on top moves it by +0.094 with an interval that
excludes zero (7 wins, 0 losses, 26 ties). `agent-no-tools` made 0 tool calls, by construction. Caveat: `AGENT_SYSTEM`
describes the four tools and allows naming files outside the candidates once a
tool confirms them, so `agent-no-tools` measures that prompt with its tools
absent. Interpretation, not tested: with no tools the model can only reorder the same ten candidates,
which `grounded` already does; with tools it can grep and read a file before
it picks. This corrects the Phase 04 reading, where the agent's lift over grounded could
not be split between prompt and tools. `agent-no-tools` does lose recall@5
(0.445 against 0.486), which the paired test above does not cover; that
difference is not tested here.

**Finding 2: the look-first nudge did not pay.** `agent-look` made more tool
calls (53% of runs used a tool against 33%, 126 calls against 71) and cost more
($0.0034 a triage against $0.0028; $0.0065 a correct answer against $0.0048),
yet its clean recall@1 is lower than `agent`'s (0.395 against 0.434): 1 win
and 6 losses on the 33 issues, difference -0.039, interval [-0.084, +0.005].
On clean rows, the primary subset, that interval includes zero, so the clean
rows alone cannot say the nudge hurts. On all 40 rows the decrease counts:
-0.049, [-0.090, -0.008], 1 win and 8 losses on 40 issues. That result counts
against the nudge. The all-rows subset includes rows that name the gold file,
so it is not the primary result, but it points the same way as the clean one
(the sign and the loss count agree). The run says the nudge did not help, cost
more, and on all rows scored lower. Against `grounded`, `agent-look` is
+0.061 with an interval that includes zero, so it is not distinguishable from
grounded here either. Mechanism, as a hypothesis this run does not test: the
nudge sends the model to tools on rows where the excerpts were enough, and a
file found by grep that is not the gold file can displace a correct top
candidate. The picks-seen share rose from 0.19 to 0.32, consistent with the
model leaning on tool output, but that statistic is not causal. `agent-look`
did raise clean recall@5 slightly (0.590 against 0.575); that is not tested
either.

**Finding 3: agency costs about 1.3 times as much per correct answer, not 1.6.**
Per triage `agent` is 1.6 times `grounded` ($0.0028 against $0.0018), but per
correct answer it is $0.0048 against $0.0037, because it is right more often.
At these prices the cost is not what decides it.

**Caveats.**
- Arm `grounded` here runs through the agent loop with the in-scope check, so it
  is not byte-identical to Phase 04's `/triage/grounded`. Its 40-row clean
  recall@1 was 33.4% and 30.4% on two Phase 04 runs; here it is 0.334 (0.304-0.365
  across repeats). The Phase 04 run-to-run spread was 3.0 points (33.4% against
  30.4%); the gap between `grounded` and `agent-no-tools` is +0.005, half a
  point, so the spread is about six times the gap. Noise swamps that gap.
- The tools read code as of the pin, after every val fix, as the retrieval
  index always has. A file the agent finds by grep may exist only because the
  fix for the issue landed, so the tool arms' gain is possibly optimistic, by an
  amount this run cannot measure.
- 40 rows, 33 clean. One clean row is ~3 points of recall@1 (one issue of 33),
  so the +0.094 and +0.099 gains are 3 rows' worth and the intervals are wide
  (the lower ends are +0.030 and +0.027). Nine wins in `grounded` vs `agent` is
  a small count.
- Three repeats per arm; the `agent` arm's spread across repeats (0.395-0.456)
  is itself 2 rows' worth.
- File-level scoring, as everywhere in Phase 03. The confirmation on the
  larger split is in the 206-row section below.

---

## Phase 05a — confirmation on all 206 val rows

The 40-row stage's decision rule chose `agent`, so the confirmation ran three
arms on every val row, one repeat each: `retrieval` (no model), `grounded`, and
`agent`. 618 records, $1.0064 in total, 3 run errors (all in `agent`), 0 api
errors. `PROVIDER=anthropic python -m eval.ablation --arms retrieval,grounded,agent --split val --n 206 --repeats 1 --max-dollars 3`.
Everything below is the output of
`python -m eval.ablation --report data/runs/val-n206-r1.jsonl`.

**Scores** (one repeat, so no min-max spread; flip rate is n/a):

| arm | subset | runs | recall@1 | recall@5 | MRR |
|---|---|---|---|---|---|
| retrieval | clean (n=175) | 175 | 0.241 | 0.540 | 0.438 |
| grounded | clean (n=175) | 175 | 0.452 | 0.584 | 0.602 |
| agent | clean (n=175) | 175 | 0.531 | 0.671 | 0.683 |
| retrieval | all | 206 | 0.261 | 0.570 | 0.464 |
| grounded | all | 206 | 0.481 | 0.614 | 0.637 |
| agent | all | 206 | 0.553 | 0.695 | 0.708 |

The `retrieval` row is the candidate order with no model call.

**Paired comparisons** (recall@1, b against a, 95% bootstrap over issues, same
method as the 40-row section):

| comparison | subset | issues | wins | losses | ties | diff | 95% interval | verdict |
|---|---|---|---|---|---|---|---|---|
| retrieval -> agent | clean | 175 | 62 | 5 | 108 | +0.290 | [+0.216, +0.364] | counts |
| retrieval -> grounded | clean | 175 | 49 | 6 | 120 | +0.211 | [+0.141, +0.280] | counts |
| grounded -> agent | clean | 175 | 22 | 8 | 145 | +0.080 | [+0.026, +0.134] | counts |
| retrieval -> agent | all | 206 | 75 | 7 | 124 | +0.293 | [+0.222, +0.363] | counts |
| retrieval -> grounded | all | 206 | 60 | 7 | 139 | +0.220 | [+0.156, +0.285] | counts |
| grounded -> agent | all | 206 | 25 | 10 | 171 | +0.072 | [+0.023, +0.124] | counts |

**Cost.**

| arm | $/triage | $/correct | p50 ms | p95 ms | run errors | api errors |
|---|---|---|---|---|---|---|
| retrieval | 0.0000 | 0.0000 | 0 | 0 | 0 | 0 |
| grounded | 0.0018 | 0.0031 | 6224 | 8636 | 0 | 0 |
| agent | 0.0031 | 0.0048 | 7270 | 12162 | 3 | 0 |

`retrieval` is free (no model call). `agent` costs more per triage ($0.0031 against $0.0018)
and per correct answer ($0.0048 against $0.0031).

**Tool use (`agent`).** 38% of runs used a tool (0.38). Turns per run:
mean 1.7, median 1, max 7. Calls by tool: grep 107, read_file 53, list_dir 6,
git_log 1. Tool error rate 0.01. The share of final picks that appeared in tool
output ("picks seen") is 0.24.

**Gate verdict.** On the 206 val rows `agent` scores higher clean recall@1 than
retrieval alone (0.531 against 0.241, +0.290, interval [+0.216, +0.364]) and
than `grounded` (0.531 against 0.452, +0.080, interval [+0.026, +0.134]), at
about 1.7 times `grounded`'s cost per triage ($0.0031 against $0.0018) and
about 1.5 times its cost per correct answer ($0.0048 against $0.0031).

The sign, size and verdict match the 40-row stage (+0.099 on its clean rows),
and the all-rows subset agrees (+0.072, interval excludes zero). The interval is
wide: its lower end is +0.026. Reading the 22 wins and 8
losses: the gain is a net of 14 issues, and 145 of 175 issues did not change.

**Caveats.**
- One repeat per row here. The 40-row stage measured run-to-run variation: the
  `agent` flip rate there was 0.212 (clean), so a single repeat on any one
  issue is noisy; the bootstrap over 175 issues accounts for sampling of issues
  but not for the model's run-to-run variation on the same issue.
- Arm `grounded` runs through the agent loop with the in-scope check, so it is
  not byte-identical to Phase 04's 206-row `/triage/grounded` result. It scores
  clean recall@1 0.452 here against 44.5% there, a 0.7 point difference; this
  run cannot say whether that is the loop or run-to-run variation.
- The tools read the code as of the pin, after every val fix. A file the agent
  finds by grep may exist only because the fix for the issue landed, so the
  gain is possibly optimistic, by an amount this run cannot measure.
- Rows whose issue text names the gold file are excluded from clean (n=175 of
  206). Clean is the subset quoted; the all-rows numbers are given for
  completeness.
- File-level scoring, as everywhere in Phase 03.
- Three `agent` runs ended in a run error, all on clean rows. They score
  recall@1 = 0, so the `agent` figure is conservative.
