# Phase 05, Pieces B + C — The live run: design

Date: 2026-11-01 · Status: approved in conversation, awaiting spec review

## Purpose

Watch the agent work as it happens: each turn, each grep and file read, the
running cost, then the answer. Today `/triage/agent` returns the whole trace at
the end. Piece A showed the tools are what help (clean recall@1 +9.4 points
from tools; 53.1% for the agent on 206 rows), so seeing tool use live is worth
building.

This covers the roadmap's remaining Phase 05 tasks:

- p5b: hard limits on turns, tokens, wall-clock and dollars
- p5c: the run as a server-sent event stream
- p5d: a `useReducer` live trace, cleaned up on unmount
- p5e: reconnects, out-of-order arrival, an abandoned tab

Pieces B and C share one spec because the event format and the UI that
consumes it must agree.

## Decisions already made

| Decision | Choice | Why |
|---|---|---|
| Abandoned tab | The run finishes | A dropped connection never wastes a half-done run; it costs at most one run's budget (~1¢). Paid turns are recorded either way. |
| Architecture | Background task + in-memory run registry (A) | Meets every requirement with the fewest parts. A Postgres event store (B) solves restarts and multi-process, which a one-machine dev setup doesn't need. Streaming out of the POST response (C) cannot reconnect. |
| Existing route | `/triage/agent` unchanged | The eval and tests use it. The live run is new routes beside it. |
| Budgets | In the loop, for every caller | Live runs, `/triage/agent` and the eval all get the same limits. |
| History | Out of scope | Browsable run history is Phase 07. |
| Frontend tests | Add Vitest (dev dependency) to `web/` | Only for the reducer, the one piece of UI logic with real edge cases. |

## 1. The loop reports steps and enforces budgets — `agent/loop.py`

`run(...)` gains two keyword parameters. Existing callers pass neither and
behave exactly as today.

```python
run(title, body, candidates, cutoff, *, system=AGENT_SYSTEM, tool_specs=None,
    max_turns=None, budget: Budget | None = None,
    on_event: Callable[[dict], None] | None = None, client=None) -> Run
```

**Events** (plain dicts with a `type` key; the loop does not number them):

| `type` | Emitted | Fields |
|---|---|---|
| `step_started` | just before each API call | `turn` |
| `usage` | when a response arrives | `turn`, `input_tokens`, `output_tokens`, `cost_usd` (that turn); `totals`: `turns`, `tokens`, `cost_usd`, `seconds` |
| `tool_called` | before each tool runs | `turn`, `tool_use_id`, `name`, `input` |
| `tool_returned` | after each tool runs | `turn`, `tool_use_id`, `name`, `output_preview` (≤2,000 chars), `is_error`, `duration_ms` |
| `done` | `end_turn` with a valid, in-scope answer | `result` (`GroundedTriageResult` as a dict), `totals` |
| `failed` | any `RunError` | `reason` (`"budget"`, `"run"` or `"api"`), `message`, `totals`; for `"budget"` also `limit` (`"turns"`, `"tokens"`, `"seconds"` or `"dollars"`) and `value` (the configured limit) |

`totals.tokens` is input plus output summed over turns. `totals.seconds` is wall
clock since the run started. A callback that raises must not break the run: the
loop catches and ignores callback exceptions.

**Budgets:**

```python
@dataclass(frozen=True)
class Budget:
    max_turns: int     # AGENT_MAX_TURNS, default 12
    max_tokens: int    # AGENT_MAX_TOKENS, default 150_000
    max_seconds: float # AGENT_MAX_SECONDS, default 180
    max_dollars: float # AGENT_MAX_DOLLARS, default 0.05
    @classmethod
    def from_config(cls) -> "Budget": ...
```

- `budget=None` means `Budget.from_config()`. `max_turns=` keeps working:
  when given, it overrides `budget.max_turns`.
- Checked **before every API call**, after the first: if `totals.turns >=
  max_turns`, or `totals.tokens >= max_tokens`, or `totals.seconds >=
  max_seconds`, or `totals.cost_usd >= max_dollars`, the loop raises
  `BudgetExceeded(RunError)` carrying the partial run, plus `limit` and `value`.
- **Stated limit:** a single API call cannot be interrupted, so a limit can be
  passed by at most one call (about a cent). Time is checked before a call,
  not during it.
- A `BudgetExceeded` is a `RunError`: callers that already handle `RunError`
  (the route, the eval, the ablation runner) need no change. The ablation runner
  records it as `error_kind "run"` (scores zero).
- The old turn-limit `RunError("no final answer after N turns")` becomes a
  `BudgetExceeded` with `limit="turns"`; its message keeps the same wording.

## 2. The run registry and routes — `api/live.py`, `api/main.py`

**Registry** (one per process, in memory, thread-safe through one
`threading.Condition`):

- Per run: `run_id` (uuid4 hex), `status` (`running`, `done` or `failed`),
  `created_at`, `finished_at`, `events: list[dict]`. Each stored event is the
  loop's dict plus `seq`, numbered from 1 per run.
- `start() -> run_id`; `append(run_id, event) -> int` (assigns `seq`, sets
  status on `done`/`failed`, notifies waiters); `events_after(run_id, seq) ->
  list[dict]`; `wait_after(run_id, seq, timeout) -> list[dict]` (blocks until
  newer events or the timeout); `get(run_id)`.
- Runs finished more than `LIVE_TTL_SECONDS` (default 900) ago are dropped,
  checked on every registry call.
- At most `LIVE_MAX_CONCURRENT` (default 2) runs with status `running`.
  `start` raises when full.

**Execution** of a run, in an asyncio task created by `POST /runs` and kept
referenced by the registry, independent of any request:

1. Retrieve the top `GROUNDED_K` candidates (thread pool), then append
   `run_started` with `candidates`: a list of `{path, score, start_line,
   end_line}`.
2. Run `agent.loop.run(..., cutoff, on_event=lambda e: registry.append(run_id,
   e))` in the thread pool.
3. Record one `calls` row per turn under `run_id`, endpoint `/runs`, exactly as
   `/triage/agent` does. The recording helper nested in `triage_agent` today is
   moved to a module-level function that both routes call.
4. A failure before the loop starts (e.g. retrieval) appends `failed` with
   `reason "api"` and records one error row, as `/triage/agent` does.

Every run ends with exactly one `done` or `failed` event.

**Routes:**

| Route | Behaviour |
|---|---|
| `POST /runs` with `AgentTriageRequest` | 202 `{"run_id": …}`. 400 unless `PROVIDER=anthropic`; 429 when `LIVE_MAX_CONCURRENT` runs are running; 422 for a malformed `created_at`. Cutoff as `/triage/agent`: `created_at` or the pin date. |
| `GET /runs/{run_id}/events` | `text/event-stream`. Starts after `Last-Event-ID` (header) or `?after=N` (query; header wins), sends all newer events, then waits for more. Each event is written as `id: <seq>`, `event: <type>`, `data: <json>`, then a blank line. A `: keep-alive` comment goes out every `LIVE_KEEPALIVE_SECONDS` (default 15) while waiting. The response ends after the `done` or `failed` event is sent. 404 for an unknown or expired run. |
| `GET /runs/{run_id}` | JSON snapshot: `run_id`, `status`, `last_seq`, `result` (when done), `failure` (when failed). 404 when unknown or expired. |
| `GET /runs/limits` | The current `Budget` as JSON (`max_turns`, `max_tokens`, `max_seconds`, `max_dollars`). No side effects; the UI uses it for the meters. Declared before `/runs/{run_id}` so `limits` is not read as a run id. |

**Stated limits:** a server restart (including `--reload`) drops runs in
progress; their paid turns are already in `calls`. Single process only.

## 3. Contract types — `api/schema.py`, `scripts/gen_types.py`

The event shapes become Pydantic models: `RunStarted`, `StepStarted`, `Usage`,
`ToolCalled`, `ToolReturned`, `Done`, `Failed`, plus `Totals` and
`LiveCandidate`. They form a discriminated union `LiveEvent` on `type`, and
every event carries `seq: int`. `gen_types.py` emits them to
`types.generated.ts`, so the reducer switches on `type` with no `?.` or `??` on
their fields. The registry validates each event against `LiveEvent` before
storing it, so the stream never sends a shape the types don't describe.

## 4. The live trace — `web/src/`

**Reducer** (`web/src/liveRun.ts`, a pure function plus its state type):

- State: `runId`, `status` (`idle`, `running`, `reconnecting`, `done` or
  `failed`), `lastSeq`, `pending` (buffered early events by `seq`),
  `candidates`, `calls` (ordered list keyed by `tool_use_id`: name, input, and
  once returned the output preview, `is_error`, `duration_ms`), `totals`,
  `result`, `failure`.
- Applies an event only when `seq === lastSeq + 1`; buffers `seq > lastSeq +
  1`; drops `seq <= lastSeq` (duplicates after a reconnect). After applying, it
  drains consecutive buffered events.
- Actions besides events: `start` (new run id, reset), `connection_lost`,
  `connection_open`.

**Component** (the Agent panel in `App.tsx`, replacing the after-the-fact panel):

- **The Agent button.** `POST /runs`, store `run_id` in `localStorage`
  (try/catch), dispatch `start`, open `EventSource(/runs/{id}/events)`.
- **Rendering:**
  - candidates as soon as `run_started` arrives
  - a trace row per tool call, "running…" until its `tool_returned`, errors
    highlighted in the existing red
  - meters against the budget (turns, tokens, dollars, seconds), with the
    limits from a small `GET /runs/limits` endpoint returning the current
    `Budget`
  - on `done`, suspected files tagged *in candidates*, *found by looking* or
    *outside candidates* (Phase 04 rule)
  - on `failed`, a banner naming the reason and, for a budget stop, the limit
- **Connection handling:**
  - `onerror` while running dispatches `connection_lost` ("reconnecting…").
    `EventSource` reconnects itself with `Last-Event-ID`; `onopen` dispatches
    `connection_open`.
  - **Close the `EventSource` as soon as `done` or `failed` is applied.**
    Otherwise it treats the stream ending as a drop and reconnects
    indefinitely.
  - The effect's cleanup closes the connection on unmount or a new run.
- **Reopen.** On page load, if `localStorage` holds a run id, `GET
  /runs/{id}`. If it exists, open the stream from `after=0`, replaying it in
  full through the reducer. If it is gone (404), clear the stored id.


## 5. Tests

No paid calls in any automated test.

- **`tests/test_loop_events.py`** (FakeClient):
  - The exact event sequence for a two-turn run (`step_started`, `usage`,
    `tool_called`, `tool_returned`, `step_started`, `usage`, `done`), with
    matching `tool_use_id`s and growing totals.
  - Each of the four limits raises `BudgetExceeded` with the right `limit` and
    `value`, keeps its paid turns, and emits one `failed` with
    `reason "budget"`.
  - A raising callback does not break the run.
  - `on_event=None` gives a `Run` identical to today's.
- **`tests/test_live_registry.py`:**
  - `seq` numbering
  - `events_after`
  - `wait_after` waking on append and returning empty on timeout
  - TTL eviction, with a time function injected
  - the concurrency limit
  - status set by `done`/`failed`
  - invalid event shapes rejected
- **`tests/test_live_routes.py`** (TestClient; the loop and retrieval faked to
  emit scripted events):
  - 202 with a run id; 400 for a non-anthropic provider; 429 at the
    concurrency limit
  - the SSE wire format
  - resume from `Last-Event-ID` and from `?after=`
  - the stream ends after `done`
  - keep-alive comments with a short interval
  - 404 for an unknown run
  - the snapshot route
  - `calls` rows per turn under the run id, the db faked as in
    `tests/test_agent_route.py`
- **`web/src/liveRun.test.ts`** (Vitest, `npm test`):
  - in-order application
  - an early event buffered, then drained when the gap fills
  - a duplicate dropped
  - `connection_lost` → `connection_open`
  - `done` sets the result
  - `failed` sets the failure with its limit
  - a full replay from 0 gives the same state as the live sequence
- **Live smoke test (paid, ~$0.02):**
  1. Start the API and the Vite dev server.
  2. `POST /runs` with the Less HMR issue; read the stream with `curl -N`.
     Expect `run_started` … `done`, numbered without gaps.
  3. Close the stream mid-run, reconnect with `Last-Event-ID`, and confirm no
     gap or duplicate.
  4. Restart one run with `AGENT_MAX_DOLLARS=0.001` to see a budget `failed`.
  5. **Hand over to the human for the browser check.**

## 6. Docs

- `ARCHITECTURE.md`: a "Live run" section covering events, the registry,
  budgets, the routes, and the stated limits (restart, single process, one-call
  overshoot).
- `README.md`: the Agent button is now live; the four budget env vars; `npm
  test` in `web/`.
- `.env.example`: `AGENT_MAX_TOKENS`, `AGENT_MAX_SECONDS`,
  `AGENT_MAX_DOLLARS`, `LIVE_MAX_CONCURRENT`, `LIVE_TTL_SECONDS`, with
  one-line comments.

## Out of scope

- A persistent run history and run browsing (Phase 07).
- Multi-process or multi-worker deployment.
- Cancelling a run from the UI.
- Prompt caching.
- Changing the agent's prompt or tools.

## Risks

- **Event loop vs threads.** The loop runs in a worker thread while the SSE
  generator awaits on the event loop. `wait_after` blocks, so the stream must
  run it in a thread pool (`run_in_threadpool`) and never call it on the event
  loop directly.
- **`--reload` during development** drops runs in progress. Stated, not fixed.
- **Proxies buffering SSE.** None in local dev. The keep-alive and
  `Cache-Control: no-cache` header cover the common cases.
