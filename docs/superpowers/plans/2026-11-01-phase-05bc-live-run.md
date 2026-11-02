# Phase 05 B+C — The Live Run Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Stream the agent's run live to the UI over SSE, with four-limit budgets enforced in the loop, an in-memory run registry that survives reconnects and abandoned tabs, and a `useReducer` trace that orders events by sequence.

**Architecture:**
- `agent.loop.run` gains `budget` and `on_event`.
- `api/schema.py` defines the event union.
- `api/live.py` holds numbered events per run and lets streams wait on them.
- `api/main.py` adds `POST /runs`, the SSE stream, a snapshot route and a limits route; the run executes in a server-owned asyncio task.
- `web/src/liveRun.ts` is a pure reducer, tested with Vitest, driving the Agent panel.

**Tech Stack:** Python 3.12, FastAPI/Starlette (`StreamingResponse`), Pydantic discriminated unions, `threading.Condition`, React + TS (`useReducer`, `EventSource`), Vitest.

**Spec:** `docs/superpowers/specs/2026-11-01-phase-05bc-live-run-design.md`

## Global Constraints

- **Budget defaults (env, `api/config.py`):** `AGENT_MAX_TURNS` 12 (exists), `AGENT_MAX_TOKENS` 150000, `AGENT_MAX_SECONDS` 180, `AGENT_MAX_DOLLARS` 0.05. `LIVE_MAX_CONCURRENT` 2, `LIVE_TTL_SECONDS` 900, `LIVE_KEEPALIVE_SECONDS` 15.
- **Event `type` values:** `run_started`, `step_started`, `usage`, `tool_called`, `tool_returned`, `done`, `failed`. `failed.reason`: `budget`, `run`, `api`. `failed.limit`: `turns`, `tokens`, `seconds`, `dollars`.
- **`seq`:** numbered from 1 per run, assigned only by the registry.
- **Budget checks:** before every API call after the first. Overshoot is at most one call (stated in docs).
- **SSE wire format:** `id: <seq>\nevent: <type>\ndata: <single-line json>\n\n`. Keep-alive is `: keep-alive\n\n`. Headers: `Cache-Control: no-cache`. The stream ends after `done` or `failed`.
- **Resume precedence:** the `Last-Event-ID` header wins over `?after=`.
- **Stability:** `/triage/agent` and every existing caller of `run` behave exactly as before. The old turn-limit message wording is kept.
- **Testing:** no paid call in automated tests. The live smoke test (Task 6) is paid, ~$0.02 total, with the default `AGENT_MAX_DOLLARS`.
- **Frontend:**
  - generated types imported, never redeclared
  - no `?.` or `??` on generated-type fields
  - `useState`/`useReducer` only
  - `localStorage` wrapped in try/catch
  - Vitest is the only new dependency (dev)
- **Git:** work on branch `phase-05bc-live-run`; one PR at the end with a merge commit. No attribution trailers.

## Review Focus

1. **Tool output containing newlines or `data:`-like text** (e.g. a grep hit `"\n\ndata: x"`). Expected: SSE framing never breaks, because each `data:` is single-line JSON that round-trips intact. Test in Task 4.
2. **Two tabs streaming the same run at once.** Expected: both receive every event, in order. Test in Task 3 (two waiters) and Task 4 (two streams).
3. **`Last-Event-ID` at or beyond the last `seq` of a finished run** (stale reload). Expected: the stream ends at once, with no hang and no error. Test in Task 4.
4. **Agent button clicked twice, or a 429.** Expected: the button is disabled while a run is `running`/`reconnecting`, and a 429 shows a readable message. Checked in Task 6, with the component logic in the reducer test where it applies.
5. **Reopening the page after the run expired.** Expected: a 404 clears the stored run id and the panel returns to idle. 404 tested in Task 4; the clearing is checked in Task 6's browser check.

---

### Task 1: Budgets and loop events

**Files:**
- Modify: `agent/loop.py`, `api/config.py`, `.env.example`
- Test: `tests/test_loop_events.py`

**Interfaces:**
- Consumes: the existing `run`, `RunError`, `Run`, `Turn` and the FakeClient pattern in `tests/test_loop.py`.
- Produces:
  - `@dataclass(frozen=True) class Budget: max_turns: int; max_tokens: int; max_seconds: float; max_dollars: float`, with `@classmethod from_config() -> Budget`.
  - `class BudgetExceeded(RunError)`: `__init__(message, run, *, limit: str, value: float)`, exposing `.limit` and `.value`.
  - `run(..., max_turns=None, budget: Budget | None = None, on_event: Callable[[dict], None] | None = None, client=None) -> Run`. `max_turns`, when given, overrides `budget.max_turns`.
  - Event dicts exactly as the spec §1 table. `totals = {"turns": int, "tokens": int, "cost_usd": float, "seconds": float}`.

- [ ] **Step 1: Failing tests** (FakeClient, scripted responses):

```python
def test_two_turn_run_emits_the_exact_sequence()
    # types == ["step_started","usage","tool_called","tool_returned","step_started","usage","done"]
    # tool_called.tool_use_id == tool_returned.tool_use_id; totals.turns goes 1 then 2; totals.tokens grows
def test_done_carries_the_result_dict()            # done["result"]["suspected_files"] == [...]
def test_turn_limit_is_a_budget_stop()             # Budget(max_turns=1,...) + tool_use response → BudgetExceeded, limit "turns", value 1; failed event reason "budget"; message contains "no final answer after 1 turns"
def test_token_limit_stops_before_the_next_call()  # turn 1 usage 10 in + 10 out, max_tokens=15 → 1 turn, limit "tokens"
def test_dollar_limit_stops_before_the_next_call() # max_dollars tiny → limit "dollars"
def test_seconds_limit_stops_before_the_next_call(monkeypatch)  # patch the loop's clock so 200 s pass → limit "seconds"
def test_run_error_emits_failed_with_reason_run()  # unparseable final text → failed reason "run"
def test_api_error_emits_failed_with_reason_api()  # client raises anthropic.APIError subclass → reason "api"
def test_raising_callback_does_not_break_the_run() # on_event raises every time → run completes, result valid
def test_no_callback_behaves_as_before()           # same script, on_event=None → same Run fields as with a no-op callback
def test_budget_from_config_reads_the_env(monkeypatch)  # AGENT_MAX_DOLLARS=0.2 → 0.2 (reload api.config or read os.getenv inside from_config)
```

- [ ] **Step 2: Run them and verify they fail.** Run: `python -m pytest tests/test_loop_events.py -q`

- [ ] **Step 3: Implement in `agent/loop.py`.**
  - Add the config values to `api/config.py`, with comments.
  - Emit events through a small `_emit(on_event, event)` that swallows exceptions from the callback.
  - Read time through a module-level `_now = time.monotonic`, so tests can patch it.
  - Run the budget check at the top of each loop iteration after the first, raising `BudgetExceeded`.
  - The existing `RunError` paths emit `failed` with the matching reason before raising: `"api"` for the `API error` wrapper, `"run"` otherwise.

- [ ] **Step 4: Add `AGENT_MAX_TOKENS`, `AGENT_MAX_SECONDS`, `AGENT_MAX_DOLLARS`** to `.env.example`, with one-line comments.

- [ ] **Step 5: Full suite.** `python -m pytest -q` → all pass (122 + new). `tests/test_loop.py`, `test_agent_route.py` and `test_ablation*.py` are unchanged and still green.

- [ ] **Step 6: Commit**: `Agent loop: four-limit budgets and step events`.

---

### Task 2: Event contract types

**Files:**
- Modify: `api/schema.py`, `scripts/gen_types.py`, `web/src/types.generated.ts` (regenerated)
- Test: `tests/test_live_schema.py`

**Interfaces:**
- Consumes: the Task 1 event dicts.
- Produces:
  - `Totals`, `LiveCandidate` (`path`, `score`, `start_line`, `end_line`).
  - `RunStarted` (`candidates`), `StepStarted`, `Usage`, `ToolCalled`, `ToolReturned`, `Done` (`result: GroundedTriageResult`, `totals`), and `Failed` (`reason`, `message`, `totals`, `limit: str | None`, `value: float | None`).
  - Each event has `type: Literal[...]` and `seq: int`, with `extra="forbid"`.
  - `LiveEvent = Annotated[Union[...], Field(discriminator="type")]` and `LIVE_EVENT = TypeAdapter(LiveEvent)`.
  - In TS, an exported union `LiveEvent` plus each member type.

- [ ] **Step 1: Failing tests:**

```python
def test_each_loop_event_validates_once_numbered()  # every event dict from Task 1's two-turn script, plus seq → LIVE_EVENT.validate_python ok
def test_unknown_type_is_rejected()
def test_extra_field_is_rejected()
def test_failed_budget_has_limit_and_value()       # Failed(reason="budget", limit="dollars", value=0.05, ...) ok; limit must be one of the four
def test_run_started_candidates()                   # [{"path","score","start_line","end_line"}]
```

- [ ] **Step 2: Verify they fail; implement.** In `gen_types.py`, emit the union and its members, following the existing `$defs` / unreachable-definitions approach. Run `python scripts/gen_types.py` and `cd web && npx tsc -b`, which must exit 0.

- [ ] **Step 3: Full suite passes. Commit**: `Live run event types: a discriminated union, generated to TypeScript`.

---

### Task 3: The run registry

**Files:**
- Create: `api/live.py`
- Test: `tests/test_live_registry.py`

**Interfaces:**
- Consumes: `LIVE_EVENT` (Task 2); config values.
- Produces:
  - `class RegistryFull(Exception)`.
  - `@dataclass class LiveRun: run_id: str; status: str; created_at: float; finished_at: float | None; events: list[dict]; task: Any = None`.
  - `class RunRegistry(*, ttl_seconds: float, max_concurrent: int, clock=time.monotonic)` with:
    - `start() -> str`
    - `append(run_id, event: dict) -> int`: adds `seq`, validates with `LIVE_EVENT`, and sets status `done`/`failed` on those types
    - `events_after(run_id, seq) -> list[dict]`
    - `wait_after(run_id, seq, timeout: float) -> list[dict]`
    - `get(run_id) -> LiveRun | None`
    - `@classmethod from_config()`
  - A module-level `registry = RunRegistry.from_config()`.
  - Unknown or expired run ids return `None` / `[]`.

- [ ] **Step 1: Failing tests** (inject a fake clock):

```python
def test_seq_numbers_from_one()
def test_events_after()
def test_wait_after_wakes_on_append()            # waiter thread blocks, append from main thread → waiter returns the new event
def test_wait_after_times_out_empty()            # timeout=0.05 → []
def test_two_waiters_both_receive()              # Review Focus 2
def test_done_and_failed_set_status_and_finished_at()
def test_finished_runs_expire_after_ttl()        # clock advances ttl+1 → get() is None
def test_running_runs_never_expire()
def test_concurrency_limit()                     # max_concurrent=2 → third start() raises RegistryFull; after one finishes, start() works
def test_invalid_event_is_rejected()             # append({"type":"nope"}) raises; nothing stored
```

- [ ] **Step 2: Verify they fail; implement** with one `threading.Condition`. Evict on every public call. Add `LIVE_MAX_CONCURRENT`, `LIVE_TTL_SECONDS` and `LIVE_KEEPALIVE_SECONDS` to `api/config.py` and `.env.example`, with comments.

- [ ] **Step 3: Full suite passes. Commit**: `Live run registry: numbered events, waiting streams, TTL, concurrency limit`.

---

### Task 4: The routes

**Files:**
- Modify: `api/main.py`, `api/models.py`
- Test: `tests/test_live_routes.py`

**Interfaces:**
- Consumes: `registry`, `RegistryFull` (Task 3); `run(..., on_event=)`, `Budget` (Task 1); the existing retrieval and route conventions in `api/main.py`.
- Produces:
  - `async def record_run_turns(run, *, run_id: str, endpoint: str, issue_number: int | None, error: str | None = None) -> None`: module level, moved out of `triage_agent`, which now calls it with unchanged behaviour.
  - `def format_sse(event: dict) -> str`.
  - `POST /runs` → 202 `{"run_id": str}`.
  - `GET /runs/limits` → the `Budget` fields. It's declared before `/runs/{run_id}`.
  - `GET /runs/{run_id}` → `{run_id, status, last_seq, result, failure}`.
  - `GET /runs/{run_id}/events` → SSE.
  - `async def _execute(run_id, request, cutoff)`: the server-owned task, stored on `LiveRun.task`.

- [ ] **Step 1: Failing tests** (TestClient without lifespan, as in `tests/test_agent_route.py`; fake retrieval, a fake `run` that calls `on_event` with a script, and a fake `record_call`):

```python
def test_post_returns_202_and_a_run_id()
def test_post_refuses_non_anthropic_provider()      # 400
def test_post_429_when_registry_full()
def test_post_422_for_malformed_created_at()
def test_stream_wire_format()                        # each block "id: n\nevent: t\ndata: {...}\n\n"; seq 1.. no gaps; run_started first; done last
def test_stream_ends_after_done()                    # client reads to EOF
def test_resume_from_last_event_id()                 # header Last-Event-ID: 3 → first id is 4
def test_header_wins_over_after_query()
def test_after_beyond_last_seq_of_finished_run_ends_at_once()   # Review Focus 3
def test_two_streams_get_the_same_events()           # Review Focus 2
def test_newlines_in_tool_output_do_not_break_framing()          # output_preview "a\n\ndata: x" round-trips via json in data line (Review Focus 1)
def test_keep_alive_comment_while_waiting(monkeypatch)           # LIVE_KEEPALIVE_SECONDS tiny, slow fake run → ": keep-alive" appears
def test_unknown_run_is_404()                        # events and snapshot
def test_snapshot_reports_status_and_result()
def test_limits_route_returns_the_budget()
def test_calls_rows_recorded_per_turn_under_the_run_id()        # endpoint "/runs"
def test_retrieval_failure_emits_failed_api_and_records_one_row()
def test_triage_agent_unchanged()                    # existing /triage/agent tests still pass (no edit needed beyond the helper move)
```

- [ ] **Step 2: Verify they fail; implement.**
  - The SSE generator calls `registry.wait_after` through `run_in_threadpool`, never on the event loop directly.
  - It emits `: keep-alive` when a wait returns empty.
  - It ends after yielding a `done` or `failed`.
  - `_execute` follows spec §2 steps 1–4.

- [ ] **Step 3: Full suite passes. Commit**: `Live run routes: POST /runs, SSE stream with resume, snapshot, limits`.

---

### Task 5: The reducer

**Files:**
- Create: `web/src/liveRun.ts`, `web/src/liveRun.test.ts`
- Modify: `web/package.json` (devDependency `vitest`, pinned to an exact version at least two weeks old; script `"test": "vitest run"`), `web/package-lock.json`

**Interfaces:**
- Consumes: the TS `LiveEvent` union and its members, plus `GroundedTriageResult` (Task 2).
- Produces:
  - `type LiveStatus = 'idle' | 'running' | 'reconnecting' | 'done' | 'failed'`.
  - `interface LiveCall { toolUseId: string; turn: number; name: string; input: Record<string, unknown>; output: string | null; isError: boolean | null; durationMs: number | null }`.
  - `interface LiveState { runId: string | null; status: LiveStatus; lastSeq: number; pending: Record<number, LiveEvent>; candidates: LiveCandidate[]; calls: LiveCall[]; totals: Totals | null; result: GroundedTriageResult | null; failure: Failed | null }`.
  - `type LiveAction = { kind: 'start'; runId: string } | { kind: 'event'; event: LiveEvent } | { kind: 'connection_lost' } | { kind: 'connection_open' }`.
  - `const initialLiveState: LiveState`, `function reduce(state: LiveState, action: LiveAction): LiveState`, `function isTerminal(s: LiveState): boolean`.

- [ ] **Step 1: Failing tests** (`cd web && npm test`), built from hand-written event fixtures:

```ts
test('applies events in order')
test('buffers an early event and drains it when the gap fills')   // seq 1,3,2 → final state == 1,2,3
test('drops a duplicate after a reconnect')                       // 1,2,2,3
test('tool_returned fills the matching call by tool_use_id')
test('connection_lost then connection_open toggles reconnecting')  // only while running
test('done sets result and status done')
test('failed sets failure with limit')
test('a full replay from 0 equals the live sequence')
test('start resets everything')
```

- [ ] **Step 2: Verify they fail; implement** the pure reducer. Don't mutate inputs.

- [ ] **Step 3: `npm test` passes; `npx tsc -b` exits 0. Commit**: `Live trace reducer: sequence-ordered, duplicate-safe, with Vitest`.

---

### Task 6: The live Agent panel, smoke test, and handover

**Files:**
- Modify: `web/src/App.tsx`, `web/src/App.css`

**Interfaces:**
- Consumes: `reduce`, `initialLiveState`, `isTerminal` (Task 5); the routes (Task 4).

- [ ] **Step 1: Implement** the panel per spec §4. It replaces the after-the-fact Agent panel; the Prose and Typed panels are unchanged.
  - `useReducer(reduce, initialLiveState)`.
  - The Agent button:
    1. `POST /runs`.
    2. On 429 or 400, show the error text.
    3. Store `run_id` in `localStorage` key `live-run-id` (try/catch).
    4. Open `EventSource`.
  - Disable the button while `running`/`reconnecting`.
  - `onerror` → `connection_lost`; `onopen` → `connection_open`. Close on `isTerminal` and in the effect cleanup.
  - Reopen on load: `GET /runs/{id}`. On 404, clear the key; otherwise stream from `after=0`.
  - Meters show totals against `GET /runs/limits`.
  - Tool rows show "running…" until returned; errors use the existing red class.
  - Done uses the Phase 04 tags; failed shows a banner naming the reason and limit.

- [ ] **Step 2: Verify.** `npx tsc -b` exits 0, `npx oxlint` is clean, `npm run build` succeeds, and `npm test` passes. No `?.` or `??` on generated-type fields.

- [ ] **Step 3: Live smoke test (paid, ~$0.02).**
  1. Start `PROVIDER=anthropic python -m uvicorn api.main:app --port 8000` (no `--reload`).
  2. `curl -s -X POST localhost:8000/runs` with the Less HMR issue. Then `curl -N localhost:8000/runs/<id>/events`. Expect `run_started` … `done`, with `id`s 1..N and no gap.
  3. Start a second run, kill the curl after the first `tool_called`, and reconnect with `-H "Last-Event-ID: <last id>"`. Expect the next id, no duplicate, ending at `done`.
  4. Restart the server with `AGENT_MAX_DOLLARS=0.001` and run once. Expect a `failed` with `reason "budget"` and `limit "dollars"`.
  5. Restart normally (defaults) and leave it running.

- [ ] **Step 4: Commit**: `Agent panel goes live: SSE trace, budget meters, reconnect and reopen`.

- [ ] **Step 5: HAND OVER TO THE HUMAN.** Report that the UI is ready. Give: the API on :8000 and the Vite dev server on :5173 running, the issue text to paste, and what to check:
  - rows appear live
  - meters move
  - closing and reopening the tab replays the run
  - the button is disabled during a run

---

### Task 7: Docs

**Files:**
- Modify: `ARCHITECTURE.md`, `README.md`

- [ ] **Step 1: `ARCHITECTURE.md`.** Add a "Live run" section covering events, the registry (numbering, TTL, concurrency), the budgets and their one-call overshoot, the routes, why the run outlives the connection, and the stated limits (restart, single process). Update the API section's route list and "Where the next phases attach".

- [ ] **Step 2: `README.md`.**
  - The Agent button is now live, with the four budget env vars.
  - `cd web && npm test` goes in the Measuring/testing table.
  - The Phase 05 status row says Pieces B+C are done.

- [ ] **Step 3: Commit**: `ARCHITECTURE, README: the live run`.
