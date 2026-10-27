# Architecture

How the pieces fit, and why each boundary is where it is. Numbers live in
`MEASUREMENTS.md`; this file says what produces them.

The project answers one question: given a GitHub issue, which files would the
real fix touch? Everything is built so the answer can be **scored** against
what the fix actually touched. That one constraint explains most of the design.

---

## The pipeline

```
                    ┌──────────────── offline, run once per corpus ────────────────┐
                    │                                                              │
  git clone ──► mining ──► data/dataset.jsonl ──► split ──► train / val / test     │
  GitHub API ─┘    │                                        (test sealed to Ph.07) │
                   │ pinned_sha.txt                                                │
                   ▼                                                               │
  repo @ pinned SHA ──► retrieval.chunk ──► retrieval.embed ──► Postgres `chunks`  │
                    └──────────────────────────────────────────────────┬───────────┘
                                                                       │
                    ┌──────────────── online, per issue ───────────────┼───────────┐
                    │                                                  ▼           │
  issue text ──► retrieval.search ──► ranked files ──► POST /retrieve, CLI, eval   │
            └──► api.triage ──► providers ──► TriageResult ──► POST /triage/*      │
                                                   │                               │
                                                   └──► Postgres `calls` (every    │
                                                        model call, incl. failures)│
                    └──────────────────────────────────────────────────────────────┘
```

Retrieval and triage are still separate paths. Joining them, so that the
top-k files are passed into `/triage/structured`, is the next step and the
lead-in to Phase 04.

---

## Layout

| Directory | Owns | Talks to |
|---|---|---|
| `mining/` | issue → fix labels, the pinned SHA, the temporal split | git, GitHub REST |
| `retrieval/` | chunking, embedding, the `chunks` table, search | Ollama (embeddings), Postgres |
| `eval/` | scoring: `retrieve` (recall@k, MRR), `classify` (labels), `failures` (readable misses) | `mining/` data, `retrieval/`, `api/` |
| `api/` | FastAPI routes, prompts, the provider seam, cost, the `calls` table | `retrieval/`, providers, Postgres |
| `web/` | one React page over the API | `api/` over HTTP |
| `scripts/` | `gen_types.py`: Pydantic → TypeScript | `api/schema.py` |

Dependencies point one way: `mining ← retrieval ← eval / api`. `mining/` imports
nothing from the rest, so ground truth can't be shaped by the code it grades.

---

## 1. Mining: ground truth before any model code

`python -m mining all` runs four stages. Each one writes a file that the next
stage reads, so re-running a later stage costs nothing:

| Stage | Reads | Writes |
|---|---|---|
| `commits` | `git log --first-parent` | `raw/commits.jsonl`, `pinned_sha.txt` |
| `fetch` | GitHub API | `raw/issues.jsonl`, `raw/pulls.jsonl` |
| `build` | the three raw files | `dataset.jsonl` + funnel report |
| `split` | `dataset.jsonl` | `splits/{train,val,test}.jsonl` |

The join it reconstructs is
`issue —(closing keyword in PR body)→ PR —(#N in commit subject)→ commit → files`.
It matches both squash subjects (`… (#123)`) and merge commits
(`Merge pull request #123`).

Four decisions are made here. Everything downstream inherits them:

- **Pinned SHA.** Labels and index both describe one commit. A label whose
  file no longer exists at the pin gets dropped, not scored as a miss.
- **Scope is one function.** `mining.config.is_gold_candidate` decides what
  counts as a source file (`SCOPE_PREFIX`, `SOURCE_EXTS`, `TEST_MARKERS`).
  `retrieval.index.in_scope_files` calls the same function, so the index
  can't contain files that could never be gold, and every gold file is in the
  index.
- **Leakage flag.** Rows whose issue text names a gold file are kept but
  marked `mentions_gold_path`. Eval reports `all` and `clean`; quote `clean`.
- **Temporal split, test sealed.** Rows are sorted by creation date. Tuning
  happens on `val`. `test` opens once, in Phase 07.

## 2. Retrieval: no model call, so every experiment is free

```
file @ pinned SHA ─► chunk(strategy) ─► embed (nomic-embed-text, 768d) ─► chunks row
issue text ─► embed_query ─► top k×12 chunks by cosine ─► collapse to files by best chunk ─► top k
```

- **Strategies are interchangeable.** Every function in `chunk.STRATEGIES`
  returns the same `Chunk` shape, and each one is stored under its own
  `strategy` value. v1, v1+header and v2 sit in the same table, so comparing
  them is a flag change, not a re-index.
- **`corpus` is in the key.** Rows are keyed on `(corpus, strategy)`, and
  `corpus` comes from `REPO_OWNER/REPO_NAME`. A second repo can share the
  table without its files leaking into Vite queries.
- **A file ranks by its single best chunk.** One very relevant chunk outranks
  several moderately relevant ones. This is a modelling choice. It's also why
  header-dominated fragments hurt v2 (see `MEASUREMENTS.md`).
- **The query is the raw issue.** Title, a blank line, then the body. The
  harness, the CLI and `/retrieve` all build it the same way, so what the API
  returns is what recall@k measured. A model-rewritten query would be a new
  strategy, not a silent change.
- **Defaults never move recorded numbers.** `STRATEGY` (default
  `fixed_v1_header`, the best measured) only sets what the CLI and API use.
  The harness always takes an explicit `--strategy`.
- **Language-neutral by default.** v1 and v1+header need no parser. v2
  resolves a tree-sitter grammar from `TREE_SITTER_LANGUAGE` and falls back
  to v1+header when the grammar is missing.

## 3. Eval: the harness is the product

`eval/retrieve.py` defines recall@k as *the fraction of each issue's gold
files* found, not hit or miss. MRR uses the first gold file found. Both are
averaged over issues and reported for `all` and `clean`.

`--split all` exists for corpora below `split.py`'s 30-row floor. It refuses to
run on any corpus that has a sealed test split.

`eval/classify.py` scores Phase 02 output against GitHub labels (severity
from `p2`/`p3`/`p4`, components from `feat: *`). It always prints the
majority-class baseline, because a constant answer once beat a real prompt.

`eval/failures.py` dumps misses as readable markdown. It answers the question
a metric can't: why didn't the right file come back?

## 4. API: every model call is accounted for

```
route ─► triage.py (owns the prompt) ─► providers.complete[_structured] ─► Completion
      ─► cost.compute_cost(Completion) ─► db.record_call ─► response
```

- **Provider seam.** `api/providers/` has two backends behind one function,
  selected once by `PROVIDER`. Ollama (`qwen2.5-coder:7b`) is the default and
  free. Anthropic (Haiku by default) costs money and is where gate numbers are
  quoted from. Both return the same `Completion`, so nothing downstream
  branches on provider except pricing.
- **`calls` table.** One row per model call, written on success *and*
  failure. Cache-token columns have existed since day one, so Phase 07's
  caching gate needs no migration. `GET /calls` reads it back.
- **Cost is computed, not estimated.** `cost.py` prices the token counts the
  provider reports, at four rates (input, output, cache write, cache read).
  Local models cost $0.
- **No ORM.** `db.py` is raw psycopg with a small pool. There are only a
  handful of queries, and each one is worth reading literally.
- **`/retrieve` and `/corpora` make no model call**, so they aren't logged to
  `calls`. They're plain `def` routes: search uses synchronous psycopg, and
  FastAPI runs it in a thread.

## 5. The typed contract: one definition, three consumers

`api/schema.py::TriageResult` is the single source of truth for structured
output:

1. Both providers constrain decoding to its JSON Schema.
2. Pydantic validates at the route. This step is **load-bearing**: neither
   provider enforces `maxLength`/`maxItems`/`maximum`, and both have violated
   them. An invalid result becomes a 500 and never reaches the browser.
3. `scripts/gen_types.py` emits `web/src/types.generated.ts`, so changing a
   field breaks the frontend build, not production. That lets `App.tsx`
   render with zero defensive field access.

Field order matters. `reasoning` comes first because constrained decoding
emits properties in schema order, so a leading reasoning field works as
chain-of-thought instead of a justification written after the verdict.

## 6. Web

`web/` is Vite + React + TypeScript with `useState` only: no component
library, no state library, no data-fetching library. It posts to
`/triage/{naive,structured}` and renders the typed result. CORS allows only
`http://localhost:5173`.

---

## Configuration and corpora

Everything is driven by `.env`. `ENV_FILE=.env.<name>` points mining,
retrieval and eval at a different corpus in one move, because
`retrieval/config.py` derives `CORPUS` from the miner's own
`REPO_OWNER`/`REPO_NAME`. `DATA_DIR` keeps each corpus's mined data separate.

The miner and the retriever are repo-agnostic: a second corpus in a different
language needed no code change. **The structured-triage prompt is not.**
`STRUCTURED_SYSTEM` in `api/triage.py` hardcodes Vite's component vocabulary,
and `eval/classify.py` maps Vite's label scheme. Either one has to change
before triage runs on another repo.

Local services: `docker compose up` starts Postgres 17 with pgvector on host
port 5433. Ollama runs separately on `:11434`. The API applies
`api/schema.sql` on startup; it is idempotent and includes the `corpus`
migration.

---

## Deliberately absent

- **No agent framework** (LangChain, LangGraph). Every layer here is something
  to explain and measure, and a framework that owns the loop hides exactly
  that. If one is ever added, Phase 06's staged workflow is where it belongs.
- **No ORM, no registry of providers, no component library.** Each would
  hide something that is currently worth reading.
- **No BM25 fusion, no abstain, no retrieval inspector yet.** All deferred
  from Phase 03 by choice, not oversight. The open-issue run showed that the
  lack of an abstain matters for issues that aren't about code.

## Where the next phases attach

| Phase | Attaches at |
|---|---|
| next step | `api/triage.py`: put `search.search_ranked` output into the structured prompt. Record it as a new endpoint or strategy so Phase 02 numbers stay comparable. |
| 04 Tools | new `read_file`/`grep`/`list_dir`/`git_log`, sandboxed to `REPO_PATH` at the pinned SHA, the same snapshot the index and labels describe |
| 05 Agent loop | sits above the provider seam. Step, token, time and dollar limits come from the same `Completion` accounting that feeds `calls`. Graded with the same recall@k as retrieval, for the A/B |
| 06 Workflow | each stage gets a typed output in the style of `TriageResult` |
| 07 Production | cache columns in `calls`, nightly `eval.retrieve`, the test split, the second corpus as a held-out generalization check |
