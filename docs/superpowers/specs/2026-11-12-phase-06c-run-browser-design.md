# Phase 06 Piece C — the run browser

**Status:** approved design, 2026-11-12. Branch `phase-06c-run-browser`.

## Goal

A second view in the web UI that walks one stored staged run stage by stage:
locate → understand → hypothesize → patch → verify. It ends with the agent's
diff next to the real fix's diff. It reads the records Pieces A and B already
wrote, so it makes **no model call and costs $0**. It doubles as the
portfolio's "where runs go wrong" demo.

## Decisions

| Question | Decision |
|---|---|
| Live or stored | Stored runs only (option A). A live staged run on `/runs` spends credit and needs minutes of Docker per verify, so it is deferred. |
| Which runs | Every staged val run: `arm == "staged"` in `data/runs/val-n40-r3.jsonl` (80) and `data/runs/val-unit-staged-r1.jsonl` (22). Patch and verify sections appear only on the runs with a patch record in `data/runs/val-n40-patch.jsonl` (29). |
| Real fix | Shown next to the agent's diff. It is computed with git on the local clone, with no model call. |
| Layout | List + detail on a `#/runs` view, with plain hash routing and no router library. |
| Turn replay | Not offered. Stored tool calls are one flat list with no stage on them, and patch records keep no tool calls. The view says so instead of guessing an attribution. |

## Backend: `api/staged_runs.py`

### Loading

- File paths are module constants. They are the two staged files above (the
  same list as `eval.patch_eval.STAGED_FILES`; import it, don't copy it) and
  the patch file `eval.patch_eval.OUT`. Nothing under `data/splits/test*` is
  read. Issue rows come from the val split only.
- **Last record wins** per `(issue, repeat)` within a file. This is the
  eval's existing rule: a resumed run appends, and an `error_kind == "api"`
  record is followed by its retry. Unlike `eval.patch_eval.eligible`, the
  loader keeps runs that have no hypothesis and runs on harness-invalid
  issues, because the browser shows failures too.
- The same `(issue, repeat)` in both staged files raises `ValueError`, the
  same invariant `eligible` enforces. Factor one shared helper rather than
  write the rule twice.
- A missing file counts as no records. `data/runs/` is git-ignored, so a
  fresh clone has none, and the list is empty rather than a 500. Blank or
  unparseable lines are skipped (reuse `eval.patch_eval.load_patch_records`'
  tolerance).
- Files are read on every request. They total about 1 MB, so there is no
  cache to invalidate.

### Real fix diff

`real_fix_diff(row) -> RealFix | None`:

- Fix commit: `eval.fixes.fix_for(row, commits)` when it returns a `Fix`. That
  is the commit the verifier graded against, so the two panes compare like
  with like. Otherwise it is the first of `eval.gold_regions.fix_commits(row,
  commits)`. With neither, it is `None`.
- Diff: `git diff <sha>^1 <sha> -- packages/vite/src`, minus every file under
  a `__tests__/` directory, through `eval.fixes._git`, so the timeout and
  `--literal-pathspecs` come with it.
- Returns `RealFix{sha, prefix_sha, diff}`. Memoised per sha with
  `functools.lru_cache`, since commits never change. A git failure returns
  `None` and logs; the endpoint never 500s on it.

### Endpoints (in `api/main.py`)

- `GET /staged-runs` → `list[StagedRunSummary]`, sorted by `(issue, repeat)`.
- `GET /staged-runs/{issue}/{repeat}` → `StagedRunDetail`. 404 when no staged
  record has that key.

### Models (`api/schema.py`, emitted to TS by `scripts/gen_types.py`)

`StagedRunSummary`:

| Field | Type | Notes |
|---|---|---|
| issue, repeat | int | |
| title | str | from the val row |
| outcome | str | the staged record's `outcome`: `hit`, `locate_miss`, `understand_miss`, `hypothesize_miss`, `error:<stage>` |
| failed_stage | str \| None | |
| cost_usd | float | the staged run's cost plus the patch's cost |
| patch_outcome | str \| None | `pass`, `test_fail`, `stale_path`, `patch_error`, or None when no patch record |

`StagedRunDetail` is the summary plus:

| Field | Type | Notes |
|---|---|---|
| body | str | issue body |
| gold | list[str] | the real fix's source files |
| stages | StagedStages | `locate: LocateResult \| None`, `understand: UnderstandResult \| None`, `hypothesis: HypothesisResult \| None`: the 06a models, validated on load |
| stage_costs | dict[str, float] | |
| error | str \| None | the failed stage's message |
| fault_hit | bool \| None | |
| tool_calls | list[StoredToolCall] | the flat stored list: `name`, `input`, `is_error`, `output_preview`. It is a new model, because the live `ToolCall` has a `turn` and the stored records do not |
| patch | PatchView \| None | `reasoning`, `edits`, `diff`, `outcome`, `cost_usd`, `error`, `verify` (`passed`, `status`, `seconds`, `tail`, `timed_out`) or None |
| real_fix | RealFix \| None | |

A stored stage that fails validation against its model becomes `None` and is
logged, so one odd record cannot 500 the endpoint. A test checks that every
stage in the real run files validates, so in practice this path is not hit.
`patch.edits` uses the existing `Edit` model.

## Frontend

### Files

- `web/src/router.ts`: `parseHash(hash) -> Route`, where Route is
  `{view: "triage"} | {view: "runs", issue?: number, repeat?: number}`, plus
  `runHash(issue, repeat)`. A malformed hash falls back to `triage`.
- `web/src/diff.ts`: `parseDiff(text) -> DiffFile[]`, where
  `DiffFile{path, lines: {kind: "add"|"del"|"ctx"|"hunk", text}[]}`. It is a
  plain unified-diff parser with no library. An empty string gives `[]`.
- `web/src/runs.tsx`: `RunsView`, with `RunList`, `RunDetail`, `StageCard`
  and `DiffPane`. Also `filterRuns(runs, {outcome, patchedOnly})`, exported
  as a pure function for the tests.
- `web/src/App.tsx`: a header with Triage | Runs. The view comes from the
  hash and follows `hashchange`. The Triage view itself is unchanged.
- `web/src/App.css`: styles for the new view.

### Behaviour

- **List (left):** each row shows `#issue rN`, a short title, the outcome
  chip, and the patch chip (✓ pass, ✗ test_fail, stale, error, or none).
  Filters: an outcome dropdown and a "patched only" checkbox. The selected
  row is the one in the hash. If the list is empty, it says "No stored runs —
  run the 06a/06b evals to populate `data/runs/`."
- **Detail (right):**
  1. The header shows the issue number (linking to
     `https://github.com/vitejs/vite/issues/<n>`), the title, the total cost,
     and the body collapsed.
  2. Stage cards come in order, each with its cost. The failed stage is red
     and shows `error`; stages after it are greyed "not reached".
     - Locate: picked files, with gold files marked ✓.
     - Understand: regions as `path:start–end`, plus `relevance`.
     - Hypothesize: fault `path:start–end` and `mechanism`, fault-hit
       yes/no/n.a., and the confidence.
     - Patch: reasoning, edit count, outcome.
     - Verify: PASS/FAIL (or timeout/infra), seconds, and the test output
       collapsed.
  3. The diffs sit side by side: "Agent" and "Real fix", coloured by line
     kind. Under 700px they stack. A missing side says why ("no patch",
     "patch error", "fix commit unknown").
  4. Tool calls: a collapsible list with a note that they are not grouped by
     stage.
- **Loading and errors:** fetch failures show an error section like the
  Triage view's. A 404 detail says the run was not found.

## Testing

- **pytest (`tests/test_staged_runs.py`):**
  - loader last-wins and api-retry handling
  - duplicate key across files raises
  - missing files give `[]`
  - malformed lines are skipped
  - patch join: present and absent
  - summary cost includes the patch's cost
  - `real_fix_diff` against a temporary git repo, showing that test files
    are excluded and a git failure gives None
  - both endpoints through FastAPI's TestClient, including the 404
  - a guard test that the loader never opens a test-split path
- **Vitest:** `parseDiff` (multi-file, hunk headers, empty), `parseHash` and
  `runHash` round-trip plus malformed input, and `filterRuns`.
- **Manual:** she opens `#/runs` in the browser and checks a passing run
  (#17676), a test_fail run, and a run that failed at understand.

## Out of scope

- Live staged runs on `/runs`, and stage events in `LiveEvent` (deferred; it
  needs credit).
- Per-stage tool-call attribution (it needs new runs).
- Any change to the eval scripts' outputs.
- The test split.
