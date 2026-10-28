# Phase 04: the SDK tool runner beside the hand-written loop

`agent/runner.py` wraps the same four functions from `agent/tools.py` with
`@beta_tool` and drives them with `client.beta.messages.tool_runner` (anthropic
1.9.0), using the same model, `max_tokens`, system prompt, `output_config` and
first message as `agent/loop.py`. It is a comparison, not a production path.
Everything below was observed by running it or read in the installed SDK source
(`.venv/lib/python3.12/site-packages/anthropic/lib/tools/`).

## 1. Schemas: generated versus hand-written

`@beta_tool` builds the schema from the signature and the docstring's `Args:`
section. Observed for `read_file(path: str, start_line: int = 1, end_line: int | None = None)`:

```
{"additionalProperties": false, "type": "object", "required": ["path"],
 "properties": {"path": {"type": "string", "title": "Path", "description": "Repo-relative file path."},
  "start_line": {"type": "integer", "default": 1, "title": "Start Line", "description": "First line, 1-based."},
  "end_line": {"anyOf": [{"type": "integer"}, {"type": "null"}], "default": null, "title": "End Line", ...}}}
```

The hand-written `TOOL_SPECS` entry for the same tool:

```
{"strict": true, "input_schema": {"type": "object",
 "properties": {"path": {...}, "start_line": {"type": ["integer", "null"], ...}, "end_line": {...}},
 "required": ["path", "start_line", "end_line"], "additionalProperties": false}}
```

Differences observed:

- No `"strict": true` on the generated tool: the output has only `name`, `description`, `input_schema`.
- Optional parameters are left out of `required` and carry a `default`. The hand-written specs list every property as required and make it nullable (`["integer", "null"]`), which is what strict mode needs.
- Nullable is `anyOf [type, null]` in the generated schema, a type array in ours.
- Noise: a `title` on every property.
- `additionalProperties: false` is present in both.
- Defaults live in the Python signature, so `git_log`'s `n=10` is applied by the function; ours applies it in `execute` for a null.

## 2. When a tool raises

Source: `_beta_runner.py:527-543` (`_run_tool`). `ToolError` becomes the error's own content; any other `Exception` is logged with `log.exception` and returned as `{"type": "tool_result", "tool_use_id": ..., "content": tool_error_content(exc), "is_error": True}`. `tool_error_content` (`_tool_dispatch.py:136-145`) returns `repr(exc)`. The loop does not stop.

Probe (one live run, issue #13950): the `grep` wrapper was made to raise
`RuntimeError("probe: disk on fire")` through a throwaway script, not committed.
(The model never called `read_file` in 3 issues, so `grep` was probed instead; the
code path is the same for every tool.) The runner's message array held this
tool_result:

```
{"type": "tool_result", "tool_use_id": "toolu_01U1jwAsdrbZT4TcFthP6LCd",
 "content": "RuntimeError('probe: disk on fire')", "is_error": true}
```

The model received `repr(exc)` with `is_error: true`; the stack trace went to the
logger (stderr), not the model. The run continued and ended in a valid answer.

Our `tools.execute` returns `"Error: RuntimeError: disk on fire"` and sets
`is_error` from the "Error: " prefix. Note the wrapper functions here return the
string from `tools.*`, whose failures are "Error: ..." strings rather than
exceptions. From `_run_tool` (line 528), a returned string never gets
`is_error`, so those failures reach the model as ordinary results without the flag.
This was read from source, not probed.

## 3. Turn limit

Yes: `tool_runner(..., max_iterations=N)` (`_beta_runner.py:153`, stored at 171).
It is checked as the `while` condition of `__run__` (`_beta_runner.py:341, 412`):
`while not self._should_stop()`, and `_iteration_count` is incremented after each
API call (line 432). The wrapper passes `C.AGENT_MAX_TURNS`.

What it does at the limit (read, not run): the loop just ends. There is no
exception and no flag; the last yielded message can still have
`stop_reason == "tool_use"`, so the caller must check it. The hand-written loop
raises `RunError("no final answer after N turns")` carrying the paid-for turns.

## 4. Message array

Source `_beta_runner.py:411-453`: after each tool turn the runner appends
`message, response` to its params (`append_messages`), where `response` is one user
message holding one `tool_result` per `tool_use`. This matches the hand-written
loop. Observed differences:

- On the closing turn the runner returns without appending the final assistant message (lines 421-426: the `stop` branch returns before `append_messages` at 431-432), so `runner._params["messages"]` lacks it. `run_with_runner` adds it back from the last yielded message. The hand-written loop appends it even if validation fails.
- The runner's `tool_result` has no `is_error` key on success; ours sets `is_error: false` explicitly.
- Assistant messages are re-serialised with `message.to_param()` (`append_messages`); ours appends `response.content` objects as received.

## 5. Answers and cost, 3 val issues

Same candidates, same model, one run each.

| issue | gold | loop | runner |
|---|---|---|---|
| 13907 | `plugins/esbuild.ts` | no files; 1 turn, 0 tools, $0.0019 | no files; 1 turn, 0 tools, $0.0018 |
| 13950 | `node/config.ts` | utils.ts, `__tests_dts__/config.ts`, config.ts (hit); 2 turns, 1 tool, $0.0034 | utils.ts, `__tests_dts__/config.ts` (miss); 2 turns, 1 tool, $0.0032 |
| 13955 | `optimizer/index.ts`, `optimizer/scan.ts` | build.ts (miss); 3 turns, 4 tools, $0.0052 | optimizer/index.ts (hit), build.ts, rolldownDepPlugin.ts, transform.ts; 1 turn, 0 tools, $0.0021 |

Three samples at temperature defaults: the differences in answers are run-to-run
variance, not a finding about the runner. The costs are comparable because the
same tokens go over the wire. Nothing was learned about accuracy.

## 6. What the runner did for us

- Generated the tool schemas from signatures and docstrings.
- Validated tool input against the signature (pydantic `validate_call`, visible in the probe traceback).
- Ran the tool loop: call the API, find `tool_use` blocks, call the function, append one `tool_result` per call in one user message, repeat until the model stops.
- Turned exceptions into `is_error` results and logged them.
- Replied "Tool 'x' not found" with `is_error` for an unknown tool (`_beta_runner.py:496-517`).
- Offered `max_iterations`.

## 7. Why the hand-written loop stays canonical

- Per-turn accounting: the loop records tokens, cache usage, stop reason, request id, latency and cost per API call and a per-tool trace (`Run.turns`, `Run.trace`). The runner yields messages, so usage is recoverable (the CLI does it from each message), but there is no tool trace and no per-tool duration.
- The turn limit: the runner stops silently at `max_iterations`; the loop raises `RunError` with the partial run, which the eval counts as a contract violation.
- Strictness: the loop's schemas are strict and hand-checked; the generated ones are not strict.
- Final-answer validation (`check_in_scope`, `RunError`) is ours either way.
- The runner is the shorter option and is fine when none of the above matter.
