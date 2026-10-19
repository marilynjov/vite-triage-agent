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

_Pending. Record recall@1 / @5 / @10 and MRR for chunking v1 before changing
anything, then again after each change._
