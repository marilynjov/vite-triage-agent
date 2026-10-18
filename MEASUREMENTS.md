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

## Phase 03 — retrieval

_Pending. Record recall@1 / @5 / @10 and MRR for chunking v1 before changing
anything, then again after each change._
