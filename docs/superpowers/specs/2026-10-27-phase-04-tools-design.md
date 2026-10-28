# Phase 04 — Tools: design

Date: 2026-10-27 · Status: approved in conversation, awaiting spec review

## Purpose

Retrieval picks files before the model has read anything. Tools let it go and
look. Phase 04 builds the tools and a hand-written tool-use loop, swaps in the
SDK's tool runner once to see what it does, and shows tool calls in the UI.

The roadmap gate: explain a tool-use conversation turn by turn, including who
appends what to the message array and why a dropped `tool_result` breaks it.

This project also measures every layer, so Phase 04 ends with a 40-row eval:
does looking beat grounded triage, and does it find gold files retrieval missed?
The full agent-vs-retrieval A/B, budgets and streaming belong to Phase 05.

## Decisions already made

| Decision | Choice | Why |
|---|---|---|
| Provider | Anthropic only (`claude-haiku-5-5`) | The gate is about the Messages API's own message array; the SDK runner (p4d) exists only there. Ollama stays on the single-call endpoints. |
| Task | Grounded + tools | Same task as `/triage/grounded` (issue + retrieval's top 10), so the only variable is whether the model could look. Baseline: recall@1 44.5%, MRR 59.5% clean (206-row val). |
| How the loop ends | Tools **and** `output_config.format` on every request | The final `end_turn` text is schema-valid `GroundedTriageResult` JSON, validated by Pydantic as today. One contract, no fake tool. Verified: the tool runner accepts `output_config` (Haiku thought, called a tool, ended on schema JSON). |
| Allowed `suspected_files` | Any path that exists at the pin and passes `is_gold_candidate` | The model can now check that a file exists, so the 68.5% recall@10 ceiling of the candidates is lifted. `/triage/grounded` keeps its candidates-only rule. |
| Eval size | 40 rows | ~$0.015 per run uncached → ~$0.60. A 206-row run (~$3) waits for Phase 05/07, when caching cuts the resent prefix. |

## 1. Tools and the sandbox — `agent/tools.py`

Four plain functions. Each is a thin wrapper over `git -C REPO_PATH` **at the
pinned SHA** (`data/pinned_sha.txt`), never the working tree, so the tools see
the snapshot the index and the labels describe. Each returns a string, the
model's input.

| Tool | Command | Output |
|---|---|---|
| `list_dir(path)` | `git ls-tree <sha> -- <path>/` | entries, directories suffixed `/` |
| `read_file(path, start_line=1, end_line=None)` | `git show <sha>:<path>` | numbered lines, at most 400 per call, headed `path: lines a-b of N` |
| `grep(pattern, path="")` | `git grep -n -I -E -e <pattern> <sha> -- <path>` | first 50 matches as `path:line: text`, plus the total count |
| `git_log(path, n=10)` | `git log <sha> --before=<cutoff> -n <n> --format=… --name-only -- <path>` | per commit: short sha, date, subject, files |

**Sandbox, enforced in code:**

- A path must be relative, contain no `..` segment after normalization, and
  must not start with `-`. The empty path means the repo root.
- Patterns go through `-e`; every user-supplied argument follows `--`. Git can
  never read either as an option.
- Results are capped as above and per call at 20,000 characters.
- Reads are allowed anywhere in the pinned tree, including tests and playground.
  Only `suspected_files` is held to the gold scope.
- A bad input (missing path, invalid regex, line range past the end) returns an
  `Error: …` string, which the loop sends back as an `is_error` result. Tools do
  not raise for model mistakes.

**`git_log` leakage cutoff.** Labels were mined from history up to the pin, so
an unfiltered `git log -- css.ts` lists the real fix commit, subject and PR
number included. Each run therefore carries a `cutoff` datetime, and `git_log`
passes `--before=<cutoff>`. The eval passes the issue's `created_at`; the API
accepts an optional `created_at` and defaults to the pin's commit date.

**Known, accepted:** `read_file` and `grep` show code as of the pin, which is
after every val fix. The retrieval index has always had the same property. It
is recorded in `ARCHITECTURE.md`, not fixed with per-issue checkouts.

## 2. The hand-written loop — `agent/loop.py`

```
run(title, body, candidates, cutoff, *, max_turns=C.AGENT_MAX_TURNS) -> Run
```

- **System:** `AGENT_SYSTEM` = `GROUNDED_SYSTEM` plus a section saying the
  candidates are a starting point, the tools can check them, and a file outside
  the list may be named once confirmed with a tool. It says what each tool is
  for and that `read_file` returns at most 400 lines per call.
- **First user message:** `triage.grounded_prompt(title, body, candidates)`.
- **Every request:** model `C.ANTHROPIC_MODEL`, `max_tokens` `C.MAX_TOKENS`, the
  same system, the same four tool definitions (hand-written JSON Schemas,
  `strict: true`, `additionalProperties: false`), `output_config.format` =
  `json_schema(GroundedTriageResult)`, and the messages so far. Thinking and
  effort stay at the model defaults.

Per response:

| `stop_reason` | Action |
|---|---|
| `tool_use` | Append `{"role": "assistant", "content": response.content}` — the whole content, thinking blocks included. Execute **every** `tool_use` block in order. Append **one** user message with a `tool_result` per block, `tool_use_id` matching, `is_error: true` when the tool returned an error. |
| `end_turn` | Parse the text block into `GroundedTriageResult`, then `check_in_scope`. Return the `Run`. |
| `max_tokens`, `refusal` | Raise `RunError`. |
| anything else | Raise `RunError` (no server tools, so `pause_turn` is unexpected). |

- **Append-only history.** Haiku 5.5 rejects a resent thinking block if anything
  before it changed. The loop never edits or reorders earlier messages.
- **Turn limit.** `AGENT_MAX_TURNS` (env, default 12) counts API calls. Reaching
  it raises `RunError`. This is a runaway guard; Phase 05 adds real budgets.
- **`RunError` carries the partial `Run`**, so callers can still record the turns
  that were paid for.
- **`Run`** holds: `result` (or None), `messages` (the full array), `turns` (per
  API call: input/output/cache tokens, stop_reason, request_id, latency, cost via
  `cost.compute_cost`), `trace` (list of `ToolCall`), and totals.
- The client is injected (`client=None` → a real `anthropic.Anthropic()`), so
  tests can pass a fake.

## 3. Contract, route, eval, runner

**Contract (`api/schema.py`).**

- `GroundedTriageResult` is unchanged.
- New `ToolCall` model (turn, name, input, output preview ≤ 2,000 chars,
  is_error, duration_ms), generated into TypeScript by `gen_types.py`.
- New `check_in_scope(result)` in `agent/tools.py`, next to the pinned-tree
  listing it uses: every path must exist in the pinned tree and pass
  `M.is_gold_candidate`; raises `ValueError`.

**Database.** `calls` gains nullable `run_id text` and `turn integer`, added by
an idempotent `alter table … add column if not exists`. `db.record_call` takes
both as optional keyword arguments; existing callers are unchanged.

**Route: `POST /triage/agent`.**

- Request: `TriageRequest` plus optional `created_at`.
- Flow:
  1. Retrieve the top `GROUNDED_K` with `search.search_ranked`, in a thread.
  2. Run the loop in a thread.
  3. Record one `calls` row per turn, endpoint `/triage/agent`, sharing a fresh
     `run_id`.
  4. Return `AgentTriageResponse`: result, candidates, trace, turns, run_id,
     summed tokens, cost, latency, model, provider.
- On `RunError` or a validation failure: record the turns that ran, with the
  error on the last row, then return a 500. If no turn completed, record one
  error row, as the other routes do.
- Refuses with 400 when `PROVIDER != "anthropic"`.

**Eval: `eval/agent.py`.**

- Rows: the same fixed slice as `eval.grounded` (val, lowest issue numbers,
  `--n 40` default).
- Per row:
  1. Retrieve the candidates once.
  2. Score the retrieval ranking.
  3. Run grounded and score it.
  4. Run the agent with `cutoff = row["created_at"]` and score it.
- All three use `score_ranking`.
- Reports, `all` and `clean`:
  - recall@1/@5 and MRR for all three, plus retrieval's recall@10 as the old
    ceiling
  - **gold found outside the candidates**: gold files the agent named that
    were not in retrieval's top 10, as a count of rows and of files
  - contract violations for grounded and for the agent
  - the distribution of turns per run
  - tool-call counts by tool
  - total cost and wall clock
- A failed run scores zero.

**p4c walkthrough: `python -m agent.explain <issue#>`.** Finds the row in val,
runs the loop once, and prints the message array turn by turn: index, role,
each block's type, `tool_use` id and name next to the `tool_result` that answers
it, is_error, and tokens per API call. `--json PATH` dumps the raw array.

**p4d runner: `agent/runner.py`.** The same four functions wrapped with
`@beta_tool`, driven by `client.beta.messages.tool_runner` with the same system,
`output_config` and first message. Not a production path. It runs on 3 val
issues next to the hand-written loop. `docs/phase-04-runner.md` records what was
observed by running it, not assumed:

- the schemas it generates from the docstrings
- what it does when a tool raises
- whether and how a turn limit can be enforced
- how its message array compares with the hand-written one

## 4. UI, tests, docs

**UI (`web/src/App.tsx`, p4e).** One "agent" panel, rendered after the response
arrives. Live streaming is Phase 05's job.

- `suspected_files`, each tagged *in candidates* or *found by looking*
- the trace table: turn, tool, arguments, result preview (expandable), duration,
  with errors highlighted
- meters: turns, tokens in/out, cost, latency, run_id

`ToolCall` and `GroundedTriageResult` are imported from `types.generated.ts`;
the response envelope is hand-declared like the existing `Envelope`. `useState`
only. `tsc -b` must pass.

**Tests (no paid calls).**

- `tests/test_tools.py`, against the real `repos/vite` at the pin:
  - rejects `..`, absolute paths, and a leading `-` in a path or pattern
  - a missing path returns an `Error:` string
  - `read_file` caps at 400 lines and reports `of N`
  - `grep` caps its matches and reports the total
  - `git_log` with a cutoff omits a commit dated after it, and includes it
    with a later cutoff
- `tests/test_loop.py`, with a fake client replaying scripted responses:
  - the assistant turn's content is appended whole, thinking included
  - every `tool_use` gets exactly one `tool_result` with the matching id, all
    in one user message
  - a tool error yields `is_error: true`
  - the turn limit raises `RunError` carrying the partial run
  - an out-of-scope final path is rejected
  - `end_turn` returns a validated result
- `tests/test_scope.py`: `check_in_scope` accepts a real in-scope source file
  and rejects a test file and a nonexistent path.
- `tests/test_runner.py` is not written. The runner is compared live, once,
  and documented.

**Docs.**

- `MEASUREMENTS.md`: first the full 206-row grounded result (recall@1 24.1% →
  44.5%, MRR 43.8% → 59.5%, recall@5 54.0% → 58.8% clean, 3/206 violations,
  $0.36), then the Phase 04 eval.
- `ARCHITECTURE.md`: an "Agent" section covering the tools, the sandbox, the
  loop, the `git_log` cutoff, and the post-fix-code caveat.
- `docs/phase-04-runner.md`.
- `.env.example`: `AGENT_MAX_TURNS`.

**Commits:** one per concern, each passing the suite on its own. No attribution
trailer.

## Out of scope

- Ollama tool use.
- Streaming and SSE.
- Token, time and dollar budgets.
- Prompt caching (Phase 07's gate; uncached cost is measured first).
- An agent run over the full val split.
- Making `STRUCTURED_SYSTEM`'s component vocabulary portable.

## Risks

- **Cost growth per turn.** Each turn resends the whole conversation. The turn
  limit bounds it, and the eval prints the total.
- **Model ignores tools.** The turn distribution will show it (all 1-turn runs).
  That would be a finding, not a bug.
- **The tool runner's behavior under exceptions and limits is not yet observed.**
  p4d exists to observe it; nothing in the production path depends on it.
