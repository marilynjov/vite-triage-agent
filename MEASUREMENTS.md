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
