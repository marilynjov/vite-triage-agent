# Phase 04: the SDK tool runner beside the hand-written loop

`agent/runner.py` wraps the same four functions from `agent/tools.py` with
`@beta_tool` and drives them with `client.beta.messages.tool_runner` (anthropic
1.9.0), using the same model, `max_tokens`, system prompt, `output_config` and
first message as `agent/loop.py`. It is a comparison, not a production path.
Everything below was observed by running it or read in the installed SDK source
(`.venv/lib/python3.12/site-packages/anthropic/lib/tools/`).

## 1. Schemas: generated versus hand-written

`@beta_tool` builds the schema from the signature and the docstring's `Args:` section. Full output of `.to_dict()` for the four wrappers (generated offline, nothing elided):

```
{"name": "list_dir", "description": "List a directory of the repository. Directories end with '/'.", "input_schema": {"additionalProperties": false, "properties": {"path": {"default": "", "description": "Repo-relative directory; empty for the repository root.", "title": "Path", "type": "string"}}, "type": "object"}}
{"name": "read_file", "description": "Read a file with line numbers, at most 400 lines per call. The header reports the file's total length.", "input_schema": {"additionalProperties": false, "properties": {"path": {"description": "Repo-relative file path.", "title": "Path", "type": "string"}, "start_line": {"default": 1, "description": "First line, 1-based.", "title": "Start Line", "type": "integer"}, "end_line": {"anyOf": [{"type": "integer"}, {"type": "null"}], "default": null, "description": "Last line; omit for as many as allowed.", "title": "End Line"}}, "required": ["path"], "type": "object"}}
{"name": "grep", "description": "Regex (extended) search; shows up to 50 matches as path:line:text.", "input_schema": {"additionalProperties": false, "properties": {"pattern": {"description": "Extended regular expression.", "title": "Pattern", "type": "string"}, "path": {"default": "", "description": "Directory or file to search; empty for everything.", "title": "Path", "type": "string"}}, "required": ["pattern"], "type": "object"}}
{"name": "git_log", "description": "Recent commits (hash, date, subject, files touched), newest first, up to 30.", "input_schema": {"additionalProperties": false, "properties": {"path": {"default": "", "description": "Limit to commits touching this path; empty for all.", "title": "Path", "type": "string"}, "n": {"default": 10, "description": "How many commits.", "title": "N", "type": "integer"}}, "type": "object"}}
```

The hand-written `TOOL_SPECS`, in full:

```
{"name": "list_dir", "description": "List a directory of the repository. Directories end with '/'.", "strict": true, "input_schema": {"type": "object", "properties": {"path": {"type": ["string", "null"], "description": "Repo-relative path; null or empty for the repo root."}}, "required": ["path"], "additionalProperties": false}}
{"name": "read_file", "description": "Read a file with line numbers, at most 400 lines per call. The header reports the file's total length.", "strict": true, "input_schema": {"type": "object", "properties": {"path": {"type": "string", "description": "Repo-relative file path."}, "start_line": {"type": ["integer", "null"], "description": "First line, 1-based; null for 1."}, "end_line": {"type": ["integer", "null"], "description": "Last line; null for as many as allowed."}}, "required": ["path", "start_line", "end_line"], "additionalProperties": false}}
{"name": "grep", "description": "Regex (extended) search; shows up to 50 matches as path:line:text.", "strict": true, "input_schema": {"type": "object", "properties": {"pattern": {"type": "string", "description": "Extended regular expression."}, "path": {"type": ["string", "null"], "description": "Directory or file to search; null for everything."}}, "required": ["pattern", "path"], "additionalProperties": false}}
{"name": "git_log", "description": "Recent commits (hash, date, subject, files touched), newest first, up to 30.", "strict": true, "input_schema": {"type": "object", "properties": {"path": {"type": ["string", "null"], "description": "Limit to commits touching this path; null for all."}, "n": {"type": ["integer", "null"], "description": "How many commits; null for 10."}}, "required": ["path", "n"], "additionalProperties": false}}
```

Differences observed:

- No `"strict": true` on the generated tool: the output has only `name`, `description`, `input_schema`.
- Optional parameters are left out of `required` and carry a `default`. The hand-written specs list every property as required and make it nullable (`["integer", "null"]`), which is what strict mode needs.
- Nullable is `anyOf [type, null]` in the generated schema, a type array in ours.
- Noise: a `title` on every property.
- `additionalProperties: false` is present in both.
- Defaults live in the Python signature, so `git_log`'s `n=10` is applied by the function; ours applies it in `execute` for a null.

## 2. When a tool raises

Source (read, not run): `_beta_runner.py:526-543` (`_run_tool`). `ToolError` becomes the error's own content; any other `Exception` is logged with `log.exception` and returned as `{"type": "tool_result", "tool_use_id": ..., "content": tool_error_content(exc), "is_error": True}`. `tool_error_content` (`_tool_dispatch.py:136-145`) returns `repr(exc)`. The loop does not stop.

Probe (one live run, issue #13950): the `grep` wrapper was made to raise
`RuntimeError("probe: disk on fire")` through a throwaway script, not committed.
(Three probe runs were made; two never reached the patched tool: the first patched `read_file`, which the model never called, and the second hit a typo that left the patch off. The third patched `grep` and is the one quoted. The code path is the same for every tool.) The runner's message array held this
tool_result:

```
{"type": "tool_result", "tool_use_id": "toolu_01U1jwAsdrbZT4TcFthP6LCd",
 "content": "RuntimeError('probe: disk on fire')", "is_error": true}
```

The model received `repr(exc)` with `is_error: true`. From source, `log.exception` (`_beta_runner.py:537`) logs the traceback, and `tool_error_content` returns only `repr(exc)` to the model. The run continued and ended in a valid answer.

Our `tools.execute` returns `"Error: RuntimeError: disk on fire"` and sets
`is_error` from the "Error: " prefix. Note the wrapper functions here return the
string from `tools.*`, whose failures are "Error: ..." strings rather than
exceptions. From `_run_tool` (line 528, source), a returned string never gets
`is_error`, so those failures reach the model as ordinary results without the flag.
This was read from source, not probed.

## 3. Turn limit

Yes: `tool_runner(..., max_iterations=N)` (`_beta_runner.py:153`, stored at 171).
It is checked as the `while` condition of `__run__` (`_beta_runner.py:340-343` for `_should_stop`, `413` for the `while`):
`while not self._should_stop()`, and `_iteration_count` is incremented after each
API call (line 432). The wrapper passes `C.AGENT_MAX_TURNS`.

What it does at the limit (read, not run): the loop just ends. There is no
exception and no flag; the last yielded message can still have
`stop_reason == "tool_use"`, so the caller must check it. The hand-written loop
raises `RunError("no final answer after N turns")` carrying the paid-for turns.

## 4. Message array (all read from source, not dumped from a run)

`_beta_runner.py:411-451`: after a tool turn the runner calls
`self.append_messages(message, response)` (line 451), where `response` is one user
message holding one `tool_result` per `tool_use`. This matches the hand-written
loop. Differences, all read from source:

- On a closing `end_turn` the `stop` branch (lines 436-439) returns before any `append_messages` call (443, 451), so `runner._params["messages"]` lacks the final assistant message. `run_with_runner` adds it back from the last yielded message when `stop_reason == "end_turn"`. The hand-written loop appends it even if validation fails.
- A successful tool result has no `is_error` key (`_beta_runner.py:528`); ours sets `is_error: false` explicitly.
- `append_messages` re-serialises assistant messages with `message.to_param()` (`_beta_runner.py:210-211`); ours appends `response.content` as received.

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
- Validated tool input against the signature (from source: `pydantic.validate_call(func)`, `_beta_functions.py:168`; not observed in a run).
- Ran the tool loop: call the API, find `tool_use` blocks, call the function, append one `tool_result` per call in one user message, repeat until the model stops.
- Turned exceptions into `is_error` results and logged them.
- Replied "Tool 'x' not found" with `is_error` for an unknown tool (`_beta_runner.py:503-514`, source only).
- Offered `max_iterations`.

## 7. Why the hand-written loop stays canonical

- Per-turn accounting: the loop records tokens, cache usage, stop reason, request id, latency and cost per API call and a per-tool trace (`Run.turns`, `Run.trace`). The runner yields messages, so usage is recoverable (the CLI does it from each message), but there is no tool trace and no per-tool duration.
- The turn limit: the runner stops silently at `max_iterations`; the loop raises `RunError` with the partial run, which the eval counts as a contract violation.
- Strictness: the loop's schemas are strict and hand-checked; the generated ones are not strict.
- Final-answer validation (`check_in_scope`, `RunError`) is ours either way.
- The runner is the shorter option and is fine when none of the above matter.
