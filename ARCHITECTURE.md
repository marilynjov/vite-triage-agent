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
            │                            │ top 10 as candidates                    │
            │                            ▼                                         │
            ├──► api.triage ──► providers ──► (Grounded)TriageResult ──► /triage/* │
            │                                       │                              │
            └──► agent.loop ⇄ agent.tools (git @ pin) ──► GroundedTriageResult     │
                 (/triage/agent, /runs)             │                              │
                                                    └──► Postgres `calls` (every   │
                                                         model call, incl. failures│
                    └──────────────────────────────────────────────────────────────┘
```

`/triage/naive` and `/triage/structured` see only the issue. `/triage/grounded`
first retrieves the top 10 files and asks the model to pick, from those only,
the ones the fix touches. It is scored by `eval/grounded.py` against retrieval
alone on the same rows. `/triage/agent` starts from the same candidates but lets
the model open files before it answers (section 7). It is scored by
`eval/agent.py`. `POST /runs` runs the same loop in the background and streams
its steps to the browser as they happen (section 8).

---

## Layout

| Directory | Owns | Talks to |
|---|---|---|
| `mining/` | issue → fix labels, the pinned SHA, the temporal split | git, GitHub REST |
| `retrieval/` | chunking, embedding, the `chunks` table, search | Ollama (embeddings), Postgres |
| `eval/` | scoring: `retrieve` (recall@k, MRR), `grounded` (model re-rank vs retrieval), `agent` (retrieval vs grounded vs agent), `ablation` (the arms, including `staged`), `gold_regions` and `attribution` (which stage missed), `classify` (labels), `failures` (readable misses) | `mining/` data, `retrieval/`, `agent/`, `api/` |
| `agent/` | the four git tools, the tool-use loop and its budgets, the staged pipeline (`pipeline`, `stages`), the SDK-runner comparison, `explain` (prints a run's message array) | `api/` (prompt, cost, schema), `retrieval/`, git |
| `api/` | FastAPI routes, prompts, the provider seam, cost, the `calls` table, the in-memory live-run registry (`live.py`) | `retrieval/`, `agent/`, providers, Postgres |
| `web/` | one React page over the API, including the live trace reducer (`liveRun.ts`) | `api/` over HTTP |
| `scripts/` | `gen_types.py`: Pydantic → TypeScript | `api/schema.py` |

There is no module-level import cycle. `mining/` imports nothing from the
rest, so ground truth can't be shaped by the code it grades. `agent/` depends
on `api/`'s contract, prompt and cost modules (`schema`, `triage`, `cost`,
`config`, `providers.base`). `api/main.py` imports `agent/` for the two agent routes (`/triage/agent` and `/runs`). The
agent's CLIs (`explain`, `runner`) import `eval.retrieve` for `query_for`.

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

`eval/grounded.py` scores the model's `suspected_files` with the same
`score_ranking` as retrieval, from the same candidates, so the only variable is
who ordered them. The model can't beat retrieval's recall@10 (it only sees the
top 10), so recall@1 and MRR are what it can move. A contract violation scores
zero.

`eval/agent.py` scores three rankings on the same rows with the same
`score_ranking`: retrieval, grounded and agent. The agent may name a file
outside the candidates, so it also reports the gold files only it could have
found. A failed run scores zero, as it is a 500 in production.

`eval/ablation.py` is the Phase 05a question, "does looking pay?": five arms
on the same rows and the same retrieved candidates (retrieval runs once per
issue). Adjacent arms differ in exactly one thing, so a score gap has one
explanation. All model arms go through `agent.loop.run(system=, tool_specs=)`,
so none differs by code path; an empty `tool_specs` omits the tools.

| arm | system prompt | tools | what it adds over the one above |
|---|---|---|---|
| retrieval | none (no model) | none | the candidate order |
| grounded | grounded re-rank | none | one model call re-ranking the candidates |
| agent-no-tools | agent prompt | none | the agent prompt |
| agent | agent prompt | four read-only tools | the tools |
| agent-look | agent prompt + look-first nudge | four read-only tools | an instruction to use them |

Each run is one JSONL record in `data/runs/<split>-n<N>-r<R>.jsonl`: `arm`,
`repeat`, `issue`, `split`, `gold`, `mentions_gold_path`, `candidates`,
`picked`, `recall_at_1`, `recall_at_5`, `rr`, `turns`, `tool_calls`,
`input_tokens`, `output_tokens`, `cost_usd`, `latency_ms`, `error` and
`error_kind`. The file is the source of truth: the run resumes from it, a
spend cap (`--max-dollars`) bounds the file's lifetime spend (the sum of
`cost_usd` over every record in it, errors included): the run refuses to start
if that sum plus the estimate exceeds the cap, and stops starting new jobs once
the file's total passes it, so resumes cannot each spend a fresh cap. The report
is computed from the file alone, so reading results costs nothing:
`python -m eval.ablation --report PATH`. Differences between arms are paired
bootstrap intervals over issues, on `clean` rows.

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
  branches on provider except pricing. The agent is the exception:
  `/triage/agent` returns 400 unless `PROVIDER=anthropic`, and the loop calls
  the Anthropic SDK directly, because tool use is written against the Messages
  API's own message array.
- **`calls` table.** One row per model call, written on success *and*
  failure. Cache-token columns have existed since day one, so Phase 07's
  caching gate needs no migration. `GET /calls` reads it back. The agent's two routes tag their rows with
  their own `endpoint` (`/triage/agent`, `/runs`), so the two paths stay
  separable in the table.
- **Routes.** `POST /triage/{naive,structured,grounded,agent}` and `POST /runs`
  make model calls. `GET /runs/limits`, `GET /runs/{id}` and
  `GET /runs/{id}/events` read a live run (section 8). `GET /calls`,
  `POST /retrieve`, `GET /corpora` and `GET /health` make none.
- **Cost is computed, not estimated.** `cost.py` prices the token counts the
  provider reports, at four rates (input, output, cache write, cache read).
  Local models cost $0.
- **Ollama's context window is set explicitly.** `num_ctx` defaults to a
  window that silently truncates a long prompt from the front. A grounded
  prompt is ~6.9k tokens, and without `OLLAMA_NUM_CTX` qwen saw 2,050 of them.
- **No ORM.** `db.py` is raw psycopg with a small pool. There are only a
  handful of queries, and each one is worth reading literally.
- **`/retrieve` and `/corpora` make no model call**, so they aren't logged to
  `calls`. They're plain `def` routes: search uses synchronous psycopg, and
  FastAPI runs it in a thread.

## 5. The typed contract: one definition, three consumers

`api/schema.py::TriageResult` is the single source of truth for structured
output. `GroundedTriageResult` subclasses it, adding `suspected_files`;
whether those paths were actually among the candidates depends on the request,
so `triage.check_candidates` enforces that after validation.

Its three consumers:

1. Both providers constrain decoding to its JSON Schema.
2. Pydantic validates at the route. This step is **load-bearing**: neither
   provider enforces `maxLength`/`maxItems`/`maximum`, and both have violated
   them. An invalid result becomes a 500 and never reaches the browser.
3. `scripts/gen_types.py` emits `web/src/types.generated.ts`, so changing a
   field breaks the frontend build, not production. That lets `App.tsx`
   render with zero defensive field access. The subclass is emitted as
   `TriageResult & { suspected_files }`.

Field order matters. `reasoning` comes first because constrained decoding
emits properties in schema order, so a leading reasoning field works as
chain-of-thought instead of a justification written after the verdict.

## 6. Web

`web/` is Vite + React + TypeScript with `useState` only: no component
library, no state library, no data-fetching library. The two triage buttons post to
`/triage/{naive,structured}` and render the typed result
(`/triage/grounded` has no button; `eval/grounded.py` and
`eval/agent.py` call `api.triage.triage_grounded` directly). The Agent button
starts a live run instead (section 8). CORS allows only
`http://localhost:5173`.

## 7. Agent: tools at the pin, a loop you can read

```
issue ─► retrieval top 10 ─► agent.loop.run ─► messages.create ─┐
                               ▲                                 │ stop_reason
                               │ ONE user message of results     ▼
                          agent.tools.execute ◄── tool_use ── end_turn ─► validate ─► check_in_scope
                          (git @ pinned SHA)
```

The agent gets four read-only tools (`list_dir`, `read_file`, `grep`,
`git_log`) and the grounded candidates as a starting point, not a boundary.
`/triage/agent` needs `PROVIDER=anthropic`, because the loop uses tool use. The
browser no longer calls it; the live run of section 8 wraps the same loop.

- **Tools read git, not the working tree.** Each one calls `git show`,
  `git grep <sha>`, `git ls-tree` or `git log` against the pinned SHA. The
  model sees the commit the index and labels describe, whatever the clone has
  checked out.
- **Paths are sandboxed before git sees them.** A path is normalised and
  rejected if it is absolute, starts with `-`, or contains `..`. Git runs with
  `--literal-pathspecs`, path arguments to `grep` and `git_log` follow `--`
  (`read_file` and `list_dir` use `<sha>:<path>`, which git cannot read as an
  option), and `grep` passes its pattern with `-e`, so a model-written string
  is a path or a pattern and never an option. A 30-second timeout bounds a
  pathological regex.
- **Output is capped, and failure is a string.** `read_file` returns at most 400
  lines, `grep` shows at most 50 matches and says how many there were, and any
  result is cut at 20,000 characters. Errors come back as text starting
  `Error: `, so the model can recover. `execute` never raises, and it sets
  `is_error` on the `tool_result`.
- **`git_log` has a cutoff the model never sees.** At the pin, history contains
  the real fix: for issue #13907 it is commit #13909. `git_log` runs with
  `--before=<cutoff>`, where the caller passes the issue's `created_at`
  (`/triage/agent` falls back to the pin's commit date when the request has
  none). Without it the agent could read the answer from the log. A test pins
  this: the fix is absent at the issue's creation time and present after it.
  The API's default cutoff is the pin date, which is right for a newly filed
  issue but means replaying an already-fixed issue through the API without
  `created_at` can show its fix in `git_log`.
- **The final answer must be in scope.** `check_in_scope` requires every
  `suspected_files` entry to exist at the pin and to pass
  `mining.config.is_gold_candidate`, the same test the labels and the index
  use. A file outside the candidates is fine once it passes. A made-up path is
  a rejected run.
- **Post-fix-code caveat.** The tools read code as of the pin, which is after
  every val fix. The agent can open a file in its already-fixed state. Retrieval
  has always had the same property, because the index is built from the same
  snapshot. Only `git_log` is cut off by date, so the agent does not get a
  cleaner view of the code than retrieval does, and this is not fixed here.

The loop in `agent/loop.py` is written by hand against `messages.create` so the
conversation can be read top to bottom:

- **`stop_reason` decides everything.** `tool_use` runs every requested tool
  and sends the next call. `end_turn` means the text block is the final JSON,
  which is validated and then checked for scope. Any other value is a
  `RunError`.
- **History is append-only.** The assistant message is `response.content`
  appended whole, thinking blocks included. Haiku 5.5 thinks by default, and a
  resent thinking block is rejected if anything before it changed.
- **One user message per turn.** Every `tool_use` needs a `tool_result` with
  the matching id, and all results for one response travel together. The
  trace keeps the first 2,000 characters of each output; the model gets the
  whole capped result.
- **Budgets stop a model that never finishes.** Section 8 lists the four
  limits; `AGENT_MAX_TURNS` is the oldest of them.
- **`RunError` carries the partial run.** A rejected answer, an unexpected
  `stop_reason` and a budget stop all raise it with `.run` attached,
  so the turns already paid for still get recorded.
- **The output schema is transformed, then enforced.** The API rejects
  constraints like `maxItems`, so `OUTPUT_SCHEMA` goes through
  `anthropic.transform_schema`. Constrained decoding is not validation, so
  Pydantic still checks the final text, as in section 5.

Accounting follows the same rule as section 4. A run is several API calls, and
`/triage/agent` writes one `calls` row per turn. The rows share a `run_id` and
carry `turn`, so a run's cost is a `GROUP BY`. A failed run still records its
turns, with the error on the last one.

`agent/explain.py` prints a run's message array turn by turn, which is the
quickest way to see the append-only rule. `agent/runner.py` drives the same
four functions with the SDK's `tool_runner`. It is a comparison on three
issues, not a production path; what it did and didn't do for us is in
[`docs/phase-04-runner.md`](docs/phase-04-runner.md).

## 8. Live run: watch the agent work, without owning the run

```
browser ─ POST /runs ─► registry.start ─► asyncio task (server-owned)
   ▲                                          │ retrieval, then agent.loop.run(on_event=)
   │ SSE: id = seq                            ▼
   └──── GET /runs/{id}/events ◄── registry (numbered events, in memory) ◄── append
```

`POST /runs` takes the same body as `/triage/agent` and returns a `run_id`
at once. The browser then reads the run's events from a separate stream. The
loop is the one from section 7; it only gained an optional `on_event`
callback and budgets.

- **Seven events, one union.** `run_started` carries the retrieved candidates.
  The loop then emits `step_started` (a turn begins), `usage` (that turn's
  tokens and cost, plus running totals), `tool_called`, `tool_returned` (a
  preview of the output, whether it errored, how long it took) and finally
  exactly one of `done` (the validated result) or `failed` (the reason, and
  the limit that tripped if it was a budget). The union lives in
  `api/schema.py` beside `TriageResult`, so the same generator emits the
  TypeScript and a field change breaks the frontend build. The registry
  validates every event on the way in, so a malformed one never reaches a
  client.
- **A callback can't break the run.** The loop emits through a wrapper that
  swallows the callback's exceptions. A broken observer costs a missing event,
  not a lost triage.
- **Budgets are four limits, checked before a call.** `AGENT_MAX_TURNS` (12),
  `AGENT_MAX_TOKENS` (150,000, input plus output), `AGENT_MAX_SECONDS` (180)
  and `AGENT_MAX_DOLLARS` (0.05). Before every API call after the first, the
  loop compares the run's totals with the four caps and raises
  `BudgetExceeded` naming the limit and its setting. **The overshoot is up to
  one call, stated plainly.** A limit is only noticed once a call has already
  made the total cross it, because the cost of a call isn't known before it
  is made. The cap is therefore a stopping point, not a hard ceiling. The
  first call is never refused, so a run always gets at least one. The caps
  are read from the environment when a run starts, and `GET /runs/limits`
  returns them so the page can show each meter against its limit.
- **The registry numbers the events.** `api/live.py` holds each run's events
  in a list and assigns `seq` itself, from 1, under one lock. Producers
  don't pick numbers, so no two events can share one, and a client can say
  "everything after N". A run is `running` until its `done` or `failed` is
  stored; after that it takes no more events. Finished runs are evicted after
  `LIVE_TTL_SECONDS` (900), which is how a page reload can still replay a
  just-finished run. Running ones are never evicted. At most
  `LIVE_MAX_CONCURRENT` (2) runs may be running at once; the next
  `POST /runs` gets a 429. Readers get copies, so a stream can't see the
  worker mutate what it is serialising.
- **Why in memory.** A run lasts under a few minutes and its record of
  record is the `calls` table, which already holds the cost. The registry is
  only a replay buffer for one browser. A table of events would add a
  migration and a write per event to protect something that is cheap to rerun.
- **Routes.** `GET /runs/{id}/events` is the stream. `GET /runs/{id}` is a
  snapshot: status, last `seq`, and the result or the failure. It's what a
  page uses to learn that a run is already over. Both 404 for an unknown or
  expired run. `/runs/limits` is declared first so "limits" isn't read as a
  run id.
- **The run outlives the connection.** The run is an `asyncio` task the
  server owns and keeps a reference to, started by the POST and not by the
  stream. Closing the tab, losing wifi or navigating away drops a reader and
  nothing else. This was a decision, not a default: the alternative, cancelling
  when the client disconnects, would make a flaky connection waste every
  call already paid for, and would make "reopen the page and see the
  answer" impossible. The cost of the choice is that an abandoned tab's run
  still spends up to its budget; the four limits are what bound that. The
  worker is also built so that whatever happens, the run ends with one `done`
  or `failed`. A crash, a cancellation or a loop that returns without a final
  event gets a `failed` appended on its behalf, so a stream is never left
  waiting for an ending that won't come.
- **Accounting is the same as section 4.** After the run, one `calls` row per
  turn, sharing the `run_id`, with `endpoint` `/runs`. A run that dies before
  its first model call records a single error row. A failure of the
  accounting itself is logged and does not undo the finished run.
- **Reconnect is a protocol feature, not page code.** Each SSE message
  carries its `seq` as the event id, so the browser's `EventSource` resends
  the last one as `Last-Event-ID` after a drop and the server replays what
  followed. A first connection can pass `?after=N` instead; the header wins
  when both are present. The stream sends a keep-alive comment after
  `LIVE_KEEPALIVE_SECONDS` (15) of silence, so a proxy doesn't close an idle
  connection while the model is thinking, and ends itself after the terminal
  event.
- **The page trusts order, not arrival.** `web/src/liveRun.ts` is a pure
  reducer. It applies an event only when its `seq` is exactly one past the last
  one applied, parks later ones until the gap fills, and drops anything
  already applied. A reconnect that replays events, or a duplicate, can't
  double-count a tool call or a total. The reducer is tested with Vitest,
  because this is the logic a flaky network would otherwise exercise for the
  first time in front of the user. The run id is kept in `localStorage`; on
  load the page asks `GET /runs/{id}` and, if the run is still known, resumes
  or shows it. A blocked `localStorage` only costs the reopen.

**Stated limits.**

- **A restart drops in-progress runs, and their `calls` rows.** The registry
  is process memory, and the rows are written when a run ends, as with
  `/triage/agent`. A restart or kill mid-run loses the events and the cost of
  the calls so far. A run that finishes before the restart is unaffected in
  `calls`, though its replay is gone.
- **One process.** The registry isn't shared, so a second worker would 404 on
  runs it didn't start. Run one uvicorn worker.
- **Open streams hold threadpool threads.** Waiting for the next event
  blocks on a condition variable in a worker thread, so each open stream
  occupies one. With one user and at most two concurrent runs that is fine; it
  would not be for a public deployment.
- **No auth.** Anyone who can reach the API can start a run, within the
  concurrency limit. CORS is the only gate, and it isn't one for non-browsers.

## 9. Staged analysis: locate, understand, hypothesize

```
issue + top 10 ─► locate ─► LocateResult ─► understand ─► UnderstandResult ─► hypothesize ─► HypothesisResult
                  (4 tools, 8 turns)        (read_file, grep, 6 turns)        (no tools, 1 turn)
                  check_locate              check_understand                  check_hypothesis
```

Phase 06's question is which stage fails most, and one agent loop can't
answer it: a miss has no address. `agent/pipeline.py::run_pipeline` runs the
same `agent.loop.run` three times, each with its own system prompt, tools,
first message and typed output. Nothing new owns the conversation, and no
framework does.

- **Three contracts.** locate returns `LocateResult` (one to five
  `LocatedFile`s: path, why, evidence). understand gets the issue and the
  located paths and returns `UnderstandResult` (one to six `Region`s: path,
  symbol, line range, what it does, relevance). hypothesize gets the issue and
  both results as JSON, has no tools, and returns `HypothesisResult`, which is
  a `GroundedTriageResult` plus a `fault` (path, line range, mechanism). So
  `suspected_files` is scored exactly like every other arm.
- **Each stage has a boundary check, in `agent/stages.py`.** locate and
  understand prune; hypothesize rejects. locate drops paths that are absent at
  the pin or fail `is_gold_candidate`, and fails only if none remain.
  understand drops regions in files locate did not name, and fails if none
  remain; a kept region must satisfy `1 <= start <= end <= file length`, or
  the whole stage fails. Both cut lists over 5 files / 6 regions instead of
  failing validation, mutate the result the loop keeps, and record what they
  cut in `PipelineRun.dropped`. A leading `./` is stripped from paths before
  comparing. hypothesize: `suspected_files` pass `check_in_scope`, `fault.path`
  is a file of an understand region, and the fault lines are inside the file;
  any miss rejects the stage. A failing check raises `ValueError` prefixed with
  the stage name, which the loop turns into a `RunError`, as in section 7.
- **The loop gained three parameters, not a second loop.** `output_model`
  (the schema sent and the type validated), `check` and `prompt` (a stage's
  prepared first message). `check=None` means the old scope check, applied only
  when `output_model` is a `GroundedTriageResult` subclass, because that check
  reads `suspected_files`. Every stage passes its own check explicitly.
- **One budget, shared.** The pipeline takes one `Budget`. Each stage gets
  its own turn cap (8, 6, 1) and what is left of the tokens, seconds and
  dollars after the stages before it. A stage that would start with any of
  those at or below zero fails at once as a budget stop, without a call. The
  overshoot is the same as section 8: up to one call past a limit, noticed
  afterwards.
- **A failure keeps its address and its spend.** `PipelineError` subclasses
  `RunError`. Its message is the failing stage's own, unchanged, so the eval's
  "API error" test on the message still works. The stage, the partial
  `PipelineRun` and the budget `limit` and `value` travel as attributes. The
  failed stage's turns are added to the run, so the cost of a failure is
  counted.
- **Eval-only, not on `/runs`.** The staged pipeline is reached through
  `eval/ablation.py` alone. `/runs` and `/triage/agent` still run the single
  agent, and `LiveEvent` has no stage events yet. Piece C attaches there.

### Gold regions: where in the file the fix was

File-level gold says which file; stage attribution needs where in it.
`eval/gold_regions.py` gets this for free, without a model call:

1. A row's fix commits are those in `raw/commits.jsonl` whose `pr_refs`
   intersect the row's `linked_prs`, or whose `closes_refs` contain the issue.
2. For each gold file, `git blame --line-porcelain <pin> -- <file>`. The lines
   blamed to a fix commit are the fix's own lines that still exist at the pin,
   already in pin line numbers. Contiguous lines form one inclusive range.

- **Per gold file.** A gold file whose fix lines did not survive (deleted,
  rewritten later) maps to `[]` and is judged at file level, even when another
  gold file of the same row has regions.
- **Blame errors raise.** Only a path absent at the pin gives `[]`. A bad
  clone path, a pin missing from the clone or a failed blame raises, so an
  infrastructure fault is never cached as "no regions".
- **The cache is keyed by pin.** `data/gold_regions.jsonl` (gitignored) holds
  `{issue, pin, regions}`; rows for another pin are ignored and recomputed.
- **Coverage.** 146 of the 206 val rows have line-level gold. The report
  counts the rest.

### Attribution: one outcome per run

`eval/attribution.py::outcome` checks in this order and returns the first that
applies, so each run has exactly one:

| Outcome | Condition |
|---|---|
| `error:<stage>` | a stage raised: `RunError`, budget or boundary check |
| `locate_miss` | no gold file in `LocateResult.files` |
| `understand_miss` | no region in a gold file overlaps that file's gold ranges (a gold file with no ranges: any region in it) |
| `hypothesize_miss` | understand reached gold, but `suspected_files[0]` is not gold |
| `hit` | `suspected_files[0]` is gold |

Ranges are inclusive and overlap when `a.start <= b.end and b.start <= a.end`.
On a `hit`, `fault_hit` records whether `fault` overlaps a gold range of its
file. It is `None`, not `False`, when it can't be judged: the row has no line
gold at all, or the fault is in a gold file that is file-level only.

### The staged arm

`staged` is a sixth entry in `eval/arms.py` with `kind="pipeline"`;
`make_record` dispatches it to `run_pipeline`. Its record is the usual one plus
`stages` (each stage's output, or null), `stage_costs`, `failed_stage`,
`dropped` (what the prune checks cut, by stage), `outcome`, `fault_hit` and
`file_level_only`, so the report stays a pure
function of the file. Gold regions load once per run, only if the staged arm is
selected, and a staged job without them is refused rather than judged at file
level by accident. Records share `data/runs/<split>-n<N>-r<R>.jsonl` with the
other arms, so staged is paired against `agent` on identical issues.

- **Infrastructure is not an outcome.** If `run_pipeline` wrapped a non-run
  exception from inside a stage (a network or SDK error, say), the record gets
  `error_kind` `api`, no `failed_stage` and no outcome, and resume retries it.
  Every api-class staged record (including an "API error" `RunError`
  raised inside a stage) carries `outcome` and `fault_hit` of `None`. Only a
  stage's own `RunError` (a rejected answer, a budget stop, a failed boundary
  check or a check with nothing left) is a result.
- **The report.** `eval/ablation_report.py` adds an outcome table (each
  outcome's share on `clean` and `all`, mean with min-max across repeats),
  the count of runs judged at file level only, the `fault_hit` rate among
  judged hits, and "Fails most": the non-hit outcome with the largest clean
  share. It also prints "Fails most by stage, miss + error (clean)": each
  stage's mean clean share of its miss plus its errors. The "Fails most" gate
  relies on this line.

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
  that. The SDK's own tool runner is kept as a comparison only (section 7).
  If one is ever added, Phase 06's staged workflow is where it belongs.
- **No ORM, no registry of providers, no component library.** Each would
  hide something that is currently worth reading.
- **No BM25 fusion, no abstain, no retrieval inspector yet.** All deferred
  from Phase 03 by choice, not oversight. The open-issue run showed that the
  lack of an abstain matters for issues that aren't about code.

## Where the next phases attach

| Phase | Attaches at |
|---|---|
| 05 Agent loop | done: the A/B (`eval/ablation.py`), budgets and the live run (section 8) |
| 06 Workflow, Piece A | done: staged analysis and attribution (section 9), eval-only |
| 06 Workflow, Piece B | patch and verify. Needs a per-issue checkout from before the fix: at the pin every val fix is already applied, so any patch would "pass". |
| 06 Workflow, Piece C | the stage-segmented trace and a diff viewer in the UI. A stage can emit through the same `on_event` seam and `LiveEvent` union, so the trace panel gains stages without a new transport; this is also where staged runs would reach `/runs` |
| 07 Production | cache columns in `calls`, nightly `eval.retrieve`, the test split, the second corpus as a held-out generalization check. Also where the live run's limits get revisited: a shared registry (or an events table) for more than one process, and auth |
