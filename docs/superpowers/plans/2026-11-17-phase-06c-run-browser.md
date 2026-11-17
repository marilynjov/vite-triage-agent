# Phase 06c Run Browser Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** A `#/runs` view that walks one stored staged run stage by stage (locate → understand → hypothesize → patch → verify) and ends with the agent's diff beside the real fix's diff, at $0.

**Architecture:** `api/staged_runs.py` reads the JSONL files Pieces A and B already wrote (last record wins per key), joins the patch record, and computes the real fix's diff with git on the local clone. Two GET routes serve typed models from `api/schema.py`, which `scripts/gen_types.py` emits to TypeScript. The frontend adds hash routing, a unified-diff parser and a list + detail view, all hand-written with no new dependencies.

**Tech Stack:** Python 3.12, FastAPI, Pydantic, pytest, git; React 19, TypeScript 6, Vitest 4.1.12.

**Spec:** `docs/superpowers/specs/2026-11-12-phase-06c-run-browser-design.md`

## Global Constraints

- No model call and no spend anywhere in this phase.
- Never read `data/splits/test*`. Issue rows come from the val split only (`eval.ablation.val_rows("val", eval.patch_eval.ALL)`).
- Staged files are `eval.patch_eval.STAGED_FILES` and the patch file is `eval.patch_eval.OUT`. Import them, don't copy them.
- Files are read on every request; no cache of run records. Only the real-fix diff (per sha) and the commits list are memoised.
- A missing file is no records. A bad record, an invalid stage or a git failure never makes an endpoint 500.
- No new npm or pip dependencies, no router library, no diff library.
- Types reach the UI only through `scripts/gen_types.py`; never hand-edit `web/src/types.generated.ts`.
- The Triage view's behaviour is unchanged.
- Commits carry no Co-Authored-By or other attribution lines.
- Use `.venv/bin/python` / `.venv/bin/pytest` (the system Python lacks `anthropic`).

## Review Focus

- A fix commit that only touched `__tests__/` files under `packages/vite/src`: the real-fix diff is `""`, and the pane says "no source changes" instead of rendering an empty box (Task 3 test, Task 6 copy).
- An `--literal-pathspecs` trap: `:!**/__tests__/**` exclude magic does nothing under it, so test files must be filtered from `--name-only` output, not by pathspec (Task 3 test).
- A diff line that starts with `---`/`+++` inside a hunk (e.g. a removed line whose text begins `--`): the parser must count it as `del`/`add`, not as a file header (Task 5 test).
- A hash like `#/runs/17676` (no repeat) or `#/runs/17676/x`: falls back to the triage view rather than crashing or selecting nothing silently (Task 5 test).
- Switching Triage → Runs → Triage mid live run: the Triage view stays mounted (hidden), so typed text and the live stream survive (Task 6 manual check).

---

### Task 1: One shared "latest staged record" rule

**Files:**
- Modify: `eval/patch_eval.py` (`eligible`)
- Test: `tests/test_patch_eval.py`

**Interfaces:**
- Produces: `patch_eval.latest_staged(staged: list[dict]) -> dict[tuple[int, int], dict]`. Input records carry `_src` (as `_load_staged` adds). Skips records whose `arm != "staged"` or `error_kind == "api"`; within one `_src` the last record wins; the same key from two different `_src` raises `ValueError` (same message as today). `eligible` calls it and keeps its own hypothesis / harness / rows / fixes filters.

- [ ] **Step 1: Write the failing tests**

```python
def test_latest_staged_last_wins_and_skips_api_and_other_arms():
    recs = [src(7, outcome="error:understand"), src(7, outcome="hit"),
            {**src(8), "error_kind": "api"}, {**src(9), "arm": "agent"}]
    got = patch_eval.latest_staged(recs)
    assert list(got) == [(7, 0)]
    assert got[(7, 0)]["outcome"] == "hit"

def test_latest_staged_raises_on_a_key_in_two_files():
    with pytest.raises(ValueError, match="duplicate staged record"):
        patch_eval.latest_staged([{**src(7), "_src": "a"}, {**src(7), "_src": "b"}])
```

Use the file's existing `src(...)` helper; extend it with `**extra` overrides if it lacks them.

- [ ] **Step 2: Run them to make sure they fail**

Run: `.venv/bin/pytest tests/test_patch_eval.py -k latest_staged -v`
Expected: FAIL with `AttributeError: ... has no attribute 'latest_staged'`

- [ ] **Step 3: Extract `latest_staged` from `eligible`'s first loop; `eligible` now starts `latest = latest_staged(staged)`**

- [ ] **Step 4: Run the whole file**

Run: `.venv/bin/pytest tests/test_patch_eval.py -v`
Expected: all pass, including the existing `test_eligible_*` tests unchanged.

- [ ] **Step 5: Commit**

```bash
git add eval/patch_eval.py tests/test_patch_eval.py
git commit -m "Factor latest_staged out of patch_eval.eligible"
```

---

### Task 2: Models and the run loader

**Files:**
- Modify: `api/schema.py` (append a `# --- Phase 06c: the run browser` section)
- Create: `api/staged_runs.py`
- Test: `tests/test_staged_runs.py`

**Interfaces:**
- Consumes: `patch_eval.latest_staged`, `patch_eval._load_staged`, `patch_eval.load_patch_records`, `patch_eval.STAGED_FILES`, `patch_eval.OUT`, `patch_eval.ALL`, `ablation.val_rows`; `LocateResult`, `UnderstandResult`, `HypothesisResult`, `Edit` from `api.schema`.
- Produces, in `api/schema.py` (each with `model_config = {"extra": "forbid"}`):
  - `StoredToolCall{name: str, input: dict[str, Any], is_error: bool, output_preview: str}`
  - `VerifyView{passed: bool, status: str, seconds: float, tail: str, timed_out: bool}`
  - `PatchView{reasoning: str | None, edits: list[Edit], diff: str | None, outcome: str | None, cost_usd: float, error: str | None, verify: VerifyView | None}`
  - `RealFix{sha: str, prefix_sha: str, diff: str}`
  - `StagedStages{locate: LocateResult | None, understand: UnderstandResult | None, hypothesis: HypothesisResult | None}`
  - `StagedRunSummary{issue: int, repeat: int, title: str, outcome: str, failed_stage: str | None, cost_usd: float, patch_outcome: str | None}`
  - `StagedRunDetail(StagedRunSummary)` adding `body: str, gold: list[str], stages: StagedStages, stage_costs: dict[str, float], error: str | None, fault_hit: bool | None, tool_calls: list[StoredToolCall], patch: PatchView | None, real_fix: RealFix | None`
- Produces, in `api/staged_runs.py`:
  - module constants `STAGED_FILES = patch_eval.STAGED_FILES`, `PATCH_FILE = patch_eval.OUT` (tests monkeypatch these; read them at call time)
  - `load_runs() -> dict[tuple[int, int], dict]` — `latest_staged` over `_load_staged` of each file
  - `load_patches() -> dict[tuple[int, int], dict]` — last record per key from `load_patch_records(PATCH_FILE)`
  - `list_runs() -> list[StagedRunSummary]`, sorted by `(issue, repeat)`
  - `get_run(issue: int, repeat: int) -> StagedRunDetail | None`
  - `real_fix_diff(row: dict) -> RealFix | None` — stubbed to return `None` in this task; Task 3 implements it. `get_run` calls it through the module (`real_fix_diff(row)` looked up at call time) so tests can monkeypatch it.

Decisions the signatures don't settle:
- `cost_usd` = staged `cost_usd` + patch `cost_usd` (0 when no patch).
- `title` / `body` come from the val row; a key whose issue has no val row gets `""` for both and `real_fix = None` (no row to look the fix up from).
- `staged_runs` imports `from eval.ablation import val_rows` by name, so tests monkeypatch `staged_runs.val_rows`.
- Each stage dict is validated with its model; a `ValidationError` makes that stage `None` and logs a warning with the key and stage name.
- `PatchView` is built from the patch record: `reasoning`/`edits` from `rec["patch"]` (or `None`/`[]` when it is null), `diff`, `outcome`, `cost_usd`, `error`; `verify` keeps only the five `VerifyView` fields.

- [ ] **Step 1: Write the failing tests**

A `files` fixture writes small staged and patch JSONL files into `tmp_path`, monkeypatches `staged_runs.STAGED_FILES` / `PATCH_FILE` to them, monkeypatches `staged_runs.val_rows` to return two fake rows, and monkeypatches `staged_runs.real_fix_diff` to `lambda row: None`. A `staged(issue, repeat=0, **extra)` helper returns a minimal valid staged record with all three stages as valid model dumps.

```python
def test_last_record_wins_and_api_retry_is_used(files):
    files.staged([staged(7, outcome="error:understand"),
                  {**staged(8), "error_kind": "api", "outcome": None}, staged(8, outcome="hit")])
    runs = {(r.issue, r.repeat): r for r in staged_runs.list_runs()}
    assert runs[(8, 0)].outcome == "hit"

def test_duplicate_key_across_files_raises(files):
    files.staged([staged(7)], [staged(7)])  # one list per file
    with pytest.raises(ValueError):
        staged_runs.list_runs()

def test_missing_files_give_empty_list(files):
    assert staged_runs.list_runs() == []

def test_malformed_lines_are_skipped(files):
    files.raw_staged(json.dumps(staged(7)) + "\n{not json\n\n")
    assert [r.issue for r in staged_runs.list_runs()] == [7]

def test_summary_without_patch(files):
    files.staged([staged(7, cost_usd=0.01)])
    (r,) = staged_runs.list_runs()
    assert (r.patch_outcome, r.cost_usd) == (None, 0.01)

def test_summary_cost_includes_the_patch(files):
    files.staged([staged(7, cost_usd=0.01)])
    files.patches([patch(7, outcome="pass", cost_usd=0.004)])
    (r,) = staged_runs.list_runs()
    assert r.patch_outcome == "pass" and r.cost_usd == pytest.approx(0.014)

def test_detail_joins_patch_and_validates_stages(files):
    files.staged([staged(7)]); files.patches([patch(7, outcome="test_fail")])
    d = staged_runs.get_run(7, 0)
    assert d.stages.hypothesis.fault.path and d.patch.verify.status == "test_fail"
    assert d.patch.edits[0].path

def test_invalid_stage_becomes_none(files):
    files.staged([staged(7, stages={"locate": {"bogus": 1}, "understand": None, "hypothesis": None})])
    assert staged_runs.get_run(7, 0).stages.locate is None

def test_unknown_key_is_none(files):
    assert staged_runs.get_run(999, 0) is None

def test_every_stage_in_the_real_files_validates():
    runs = staged_runs.load_runs()
    if not runs:
        pytest.skip("data/runs/ not populated")
    for key, rec in runs.items():
        for name, model in (("locate", LocateResult), ("understand", UnderstandResult),
                            ("hypothesis", HypothesisResult)):
            if rec["stages"][name] is not None:
                model.model_validate(rec["stages"][name])
```

- [ ] **Step 2: Run them to make sure they fail**

Run: `.venv/bin/pytest tests/test_staged_runs.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'api.staged_runs'`

- [ ] **Step 3: Add the models to `api/schema.py`, then implement `api/staged_runs.py`**

- [ ] **Step 4: Run the tests**

Run: `.venv/bin/pytest tests/test_staged_runs.py -v`
Expected: all pass (the real-files test passes against the local `data/runs/`: 80 + 22 staged runs).

- [ ] **Step 5: Commit**

```bash
git add api/schema.py api/staged_runs.py tests/test_staged_runs.py
git commit -m "Staged run loader: last record wins, patch joined, stages validated"
```

---

### Task 3: The real fix's diff

**Files:**
- Modify: `api/staged_runs.py`
- Test: `tests/test_staged_runs.py`

**Interfaces:**
- Consumes: `eval.fixes.fix_for`, `eval.fixes._git`, `eval.gold_regions.fix_commits`, `eval.verify_harness.load_commits`.
- Produces: `real_fix_diff(row: dict) -> RealFix | None` (replaces the stub) and `_diff_at(sha: str) -> RealFix | None`, decorated `functools.lru_cache`. Commits come from a `functools.lru_cache`d `_commits()` wrapping `load_commits()` (3.3 MB; read once per process).

Decisions:
- Sha: `fix_for(row, _commits()).fix_sha` when it returns a `Fix`; else `fix_commits(row, _commits())[0]`; neither → `None`.
- In `_diff_at`: `prefix = rev-parse <sha>^1`; paths = `diff --name-only --no-renames <prefix> <sha> -- packages/vite/src`, minus every path containing `/__tests__/`. No paths left → `RealFix(diff="")` without a second git call. Otherwise `diff --no-renames <prefix> <sha> -- <paths...>`. All through `fixes._git`. **Do not** use `:!` exclude pathspecs: `_git` passes `--literal-pathspecs`, which disables them.
- Any non-zero git return code or exception (`subprocess.TimeoutExpired`, `RuntimeError` from `fix_for`) → log a warning and return `None`.

- [ ] **Step 1: Write the failing tests**

A `repo` fixture `git init`s a repo in `tmp_path` with a base commit, then a fix commit that changes `packages/vite/src/node/a.ts` and `packages/vite/src/node/__tests__/a.spec.ts`, and monkeypatches `mining.config.REPO_PATH` to it. Clear `_diff_at.cache_clear()` in the fixture.

```python
def test_real_fix_diff_excludes_test_files(repo):
    fix = staged_runs._diff_at(repo.fix_sha)
    assert fix.prefix_sha == repo.base_sha
    assert "node/a.ts" in fix.diff and "__tests__" not in fix.diff

def test_fix_touching_only_tests_gives_empty_diff(repo):
    sha = repo.commit({"packages/vite/src/node/__tests__/a.spec.ts": "x\n"})
    assert staged_runs._diff_at(sha).diff == ""

def test_git_failure_gives_none(repo):
    assert staged_runs._diff_at("0" * 40) is None

def test_row_with_no_fix_commit_gives_none(monkeypatch):
    monkeypatch.setattr(staged_runs, "_commits", lambda: [])
    assert staged_runs.real_fix_diff({"issue": 1, "linked_prs": []}) is None

def test_falls_back_to_first_fix_commit(repo, monkeypatch):
    # no unit test in this commit, so fix_for returns None
    sha = repo.commit({"packages/vite/src/node/a.ts": "y\n"})
    monkeypatch.setattr(staged_runs, "_commits", lambda: [{"sha": sha, "closes_refs": [5]}])
    assert staged_runs.real_fix_diff({"issue": 5, "linked_prs": []}).sha == sha
```

- [ ] **Step 2: Run them to make sure they fail**

Run: `.venv/bin/pytest tests/test_staged_runs.py -k "real_fix or fix_touching or git_failure or fix_commit" -v`
Expected: FAIL (`_diff_at` missing, stub returns `None`)

- [ ] **Step 3: Implement `_commits`, `_diff_at`, `real_fix_diff`**

- [ ] **Step 4: Run the whole file**

Run: `.venv/bin/pytest tests/test_staged_runs.py -v`
Expected: all pass.

- [ ] **Step 5: Spot-check against the real clone**

Run: `.venv/bin/python -c "from api import staged_runs as s; d=s.get_run(17676,0); print(d.real_fix.sha, len(d.real_fix.diff), d.patch.outcome)"`
Expected: a 40-char sha, a non-zero length, `pass`. (If 17676 has no repeat 0, use the first `pass` key from `list_runs()`.)

- [ ] **Step 6: Commit**

```bash
git add api/staged_runs.py tests/test_staged_runs.py
git commit -m "Real fix diff from git, test files excluded, memoised per sha"
```

---

### Task 4: Endpoints and generated types

**Files:**
- Modify: `api/main.py`, `scripts/gen_types.py`, `web/src/types.generated.ts` (regenerated), `ARCHITECTURE.md` (routes line)
- Test: `tests/test_staged_runs.py`

**Interfaces:**
- Consumes: `staged_runs.list_runs`, `staged_runs.get_run`.
- Produces: `GET /staged-runs` → `list[StagedRunSummary]`; `GET /staged-runs/{issue}/{repeat}` → `StagedRunDetail`, 404 `{"detail": "no staged run for issue <i> repeat <r>"}`. Both plain `def` (file and git I/O run in the threadpool). TS types named `StagedRunSummary`, `StagedRunDetail`, `StagedStages`, `PatchView`, `VerifyView`, `RealFix`, `StoredToolCall`, `LocateResult`, `UnderstandResult`, `HypothesisResult`, `Edit`, `Fault`, `Region`, `LocatedFile`.

Decision for `gen_types.py`: split the body of `live_defs` into `named_defs(models: list[type[BaseModel]]) -> dict` (the per-model loop: `ref_template="#/$defs/{model}"`, pop nested `$defs`, title = class name, pop property titles). `live_defs` calls it and keeps its `Done` override and `LiveEvent` union. Add `STAGED_MODELS` listing every model above (dependencies included) and merge `named_defs(STAGED_MODELS)` into `$defs`.

- [ ] **Step 1: Write the failing tests**

A `client` fixture copies `tests/test_live_routes.py`'s `env` pattern (no-op `db.connect`/`db.disconnect`) and reuses Task 2's `files` fixture.

```python
def test_list_endpoint(client, files):
    files.staged([staged(8), staged(7, repeat=1)])
    body = client.get("/staged-runs").json()
    assert [(r["issue"], r["repeat"]) for r in body] == [(7, 1), (8, 0)]

def test_detail_endpoint_and_404(client, files):
    files.staged([staged(7)])
    assert client.get("/staged-runs/7/0").json()["stages"]["locate"]["files"]
    r = client.get("/staged-runs/7/3")
    assert r.status_code == 404 and "issue 7 repeat 3" in r.json()["detail"]

def test_loader_never_opens_a_test_split_path(client, tmp_path, monkeypatch):
    # set up by hand, not via `files`: the guard needs the real val_rows and real_fix_diff
    sf = tmp_path / "s.jsonl"; sf.write_text(json.dumps(staged(17676)) + "\n")
    monkeypatch.setattr(staged_runs, "STAGED_FILES", (sf,))
    monkeypatch.setattr(staged_runs, "PATCH_FILE", tmp_path / "none.jsonl")
    opened = []
    real_open, real_read = builtins.open, pathlib.Path.read_text
    monkeypatch.setattr(builtins, "open", lambda p, *a, **k: (opened.append(str(p)), real_open(p, *a, **k))[1])
    monkeypatch.setattr(pathlib.Path, "read_text", lambda self, *a, **k: (opened.append(str(self)), real_read(self, *a, **k))[1])
    client.get("/staged-runs"); client.get("/staged-runs/17676/0")
    assert opened and not [p for p in opened if "splits/test" in p]
```

- [ ] **Step 2: Run them to make sure they fail**

Run: `.venv/bin/pytest tests/test_staged_runs.py -k "endpoint or test_split" -v`
Expected: FAIL with 404 on `/staged-runs`

- [ ] **Step 3: Add both routes to `api/main.py`**

- [ ] **Step 4: Run the full Python suite**

Run: `.venv/bin/pytest -q`
Expected: all pass (Docker tests skipped as usual).

- [ ] **Step 5: Regenerate the types and typecheck**

Run: `.venv/bin/python scripts/gen_types.py && (cd web && npx tsc -b)`
Expected: `→ web/src/types.generated.ts`; `grep -c "export interface StagedRunDetail" web/src/types.generated.ts` prints `1`; no `LocateResult1`-style numbered aliases; `tsc` clean.

- [ ] **Step 6: Add the two routes to the Routes bullet in `ARCHITECTURE.md`, then commit**

```bash
git add api/main.py scripts/gen_types.py web/src/types.generated.ts ARCHITECTURE.md tests/test_staged_runs.py
git commit -m "GET /staged-runs and /staged-runs/{issue}/{repeat}, types emitted"
```

---

### Task 5: Hash router and diff parser

**Files:**
- Create: `web/src/router.ts`, `web/src/diff.ts`, `web/src/router.test.ts`, `web/src/diff.test.ts`

**Interfaces:**
- Produces:
  - `type Route = { view: 'triage' } | { view: 'runs'; issue?: number; repeat?: number }`
  - `parseHash(hash: string): Route` — `''`, `'#'`, `'#/'` → triage; `'#/runs'` → runs; `'#/runs/<int>/<int>'` → runs with both; anything else → triage.
  - `runHash(issue: number, repeat: number): string` → `'#/runs/<issue>/<repeat>'`
  - `type DiffLine = { kind: 'add' | 'del' | 'ctx' | 'hunk'; text: string }`, `type DiffFile = { path: string; lines: DiffLine[] }`
  - `parseDiff(text: string): DiffFile[]`

Parser rule: a `diff --git a/X b/Y` line starts a file with path `Y`. Until the first `@@` of that file, every other line (`index`, `---`, `+++`, `new file mode`, …) is header and dropped. From the first `@@`: `@@…` → `hunk`, `+…` → `add`, `-…` → `del`, ` …` → `ctx` (text without the marker), `\ No newline at end of file` dropped. Inside a hunk, `---x` is a `del` of `--x`.

- [ ] **Step 1: Write the failing tests**

```ts
test('parseHash round-trips runHash', () => {
  expect(parseHash(runHash(17676, 2))).toEqual({ view: 'runs', issue: 17676, repeat: 2 })
})
test('parseHash: list and triage', () => {
  expect(parseHash('#/runs')).toEqual({ view: 'runs' })
  expect(parseHash('')).toEqual({ view: 'triage' })
})
test.each(['#/runs/17676', '#/runs/17676/x', '#/runs/-1/0', '#/nope', '#/runs/1/2/3'])(
  'malformed %s falls back to triage', (h) => expect(parseHash(h)).toEqual({ view: 'triage' }))

const TWO = `diff --git a/p/a.ts b/p/a.ts
index 1..2 100644
--- a/p/a.ts
+++ b/p/a.ts
@@ -1,2 +1,2 @@ fn
 keep
-old
+new
diff --git a/p/b.ts b/p/b.ts
--- a/p/b.ts
+++ b/p/b.ts
@@ -5 +5 @@
----x
+++y
`
test('parseDiff: two files, hunks, kinds', () => {
  const files = parseDiff(TWO)
  expect(files.map((f) => f.path)).toEqual(['p/a.ts', 'p/b.ts'])
  expect(files[0].lines.map((l) => l.kind)).toEqual(['hunk', 'ctx', 'del', 'add'])
  expect(files[0].lines[2].text).toBe('old')
  expect(files[1].lines.slice(1)).toEqual([{ kind: 'del', text: '---x' }, { kind: 'add', text: '++y' }])
})
test('parseDiff: empty', () => expect(parseDiff('')).toEqual([]))
```

- [ ] **Step 2: Run them to make sure they fail**

Run: `cd web && npx vitest run src/router.test.ts src/diff.test.ts`
Expected: FAIL, cannot resolve `./router` / `./diff`

- [ ] **Step 3: Implement both modules**

- [ ] **Step 4: Run them to make sure they pass**

Run: `cd web && npx vitest run`
Expected: all pass, `liveRun.test.ts` included.

- [ ] **Step 5: Commit**

```bash
git add web/src/router.ts web/src/diff.ts web/src/router.test.ts web/src/diff.test.ts
git commit -m "Hash router and unified-diff parser, no libraries"
```

---

### Task 6: The run browser view

**Files:**
- Create: `web/src/runs.tsx`, `web/src/runs.test.ts`
- Modify: `web/src/App.tsx`, `web/src/App.css`

**Interfaces:**
- Consumes: `parseHash`, `runHash`, `parseDiff` (Task 5); `StagedRunSummary`, `StagedRunDetail` from `./types.generated` (Task 4).
- Produces: `export function RunsView({ issue, repeat }: { issue?: number; repeat?: number })` and `export function filterRuns(runs: StagedRunSummary[], f: { outcome: string; patchedOnly: boolean }): StagedRunSummary[]` (`outcome === ''` means any; `patchedOnly` keeps rows with `patch_outcome !== null`). `RunList`, `RunDetail`, `StageCard`, `DiffPane` are internal.

Decisions:
- `App.tsx`: rename the current `App` to `TriageView` (body untouched). New default `App` holds `route` state from `parseHash(location.hash)`, updated on `hashchange` (listener removed on unmount), renders a `<header>` with links `#/` "Triage" and `#/runs` "Runs" (the active one marked `aria-current="page"`), then `<div hidden={route.view !== 'triage'}><TriageView /></div>` (always mounted, so a live run and typed text survive a switch) and `{route.view === 'runs' && <RunsView … />}`.
- `RunsView` fetches `${API}/staged-runs` once on mount and `${API}/staged-runs/${issue}/${repeat}` whenever the selected key changes (ignore a stale response with a `cancelled` flag like `App.tsx` does). `const API` moves from `App.tsx` into a new one-line `web/src/api.ts`, imported by both.
- Outcome dropdown options are the distinct `outcome`s present, sorted, plus "any".
- Stage cards in order locate, understand, hypothesize, then patch and verify only when `detail.patch` is non-null. A card is red and shows `detail.error` when its name equals `failed_stage`; cards after it are greyed "not reached". Costs come from `stage_costs[name]` (patch card: `patch.cost_usd`; verify card shows no cost).
- Verify card: "PASS" when `status === 'pass'`, "FAIL" for `test_fail`, "TIMEOUT" when `timed_out`, otherwise the raw status (infra).
- Diff pane empty-side copy, exactly: agent side `"no patch"` (no patch record), `"no patch (stale path)"` (`stale_path`), `"patch error"` (`patch_error`); real side `"fix commit unknown"` (`real_fix === null`), `"no source changes"` (`real_fix.diff === ''`).
- Empty list copy, exactly: `No stored runs — run the 06a/06b evals to populate data/runs/.` A 404 on detail shows `Run not found.`; other fetch failures reuse the `.error` section markup from the Triage view.
- Tool calls: `<details>` with summary `Tool calls (N) — stored as one list, not grouped by stage`.
- CSS: list + detail as a two-column grid; diffs side by side, stacked under `700px` (`@media (max-width: 700px)`); `.add`/`.del`/`.hunk` line colours reuse the existing tokens in `index.css`, and add dark-mode-safe translucent backgrounds like `.chip.sev-high` does.

- [ ] **Step 1: Write the failing test**

```ts
const r = (issue: number, outcome: string, patch_outcome: string | null): StagedRunSummary =>
  ({ issue, repeat: 0, title: 't', outcome, failed_stage: null, cost_usd: 0, patch_outcome })
const RUNS = [r(1, 'hit', 'pass'), r(2, 'hit', null), r(3, 'locate_miss', 'test_fail')]

test('filterRuns: any outcome, all', () => expect(filterRuns(RUNS, { outcome: '', patchedOnly: false })).toHaveLength(3))
test('filterRuns: by outcome', () =>
  expect(filterRuns(RUNS, { outcome: 'hit', patchedOnly: false }).map((x) => x.issue)).toEqual([1, 2]))
test('filterRuns: patched only', () =>
  expect(filterRuns(RUNS, { outcome: '', patchedOnly: true }).map((x) => x.issue)).toEqual([1, 3]))
```

- [ ] **Step 2: Run it to make sure it fails**

Run: `cd web && npx vitest run src/runs.test.ts`
Expected: FAIL, cannot resolve `./runs`

- [ ] **Step 3: Implement `api.ts`, `runs.tsx`, the `App.tsx` shell and the CSS**

- [ ] **Step 4: Tests, typecheck, lint, build**

Run: `cd web && npx vitest run && npm run lint && npm run build`
Expected: all tests pass, no lint errors, build succeeds.

- [ ] **Step 5: Commit**

```bash
git add web/src/api.ts web/src/runs.tsx web/src/runs.test.ts web/src/App.tsx web/src/App.css
git commit -m "Run browser: #/runs list and stage-by-stage detail with both diffs"
```

---

### Task 7: Her check in the browser

No code unless the check finds a bug (then fix it with a test in the owning task's file).

- [ ] **Step 1:** Start the API and the dev server the usual way (`uvicorn api.main:app --reload`, `cd web && npm run dev`).
- [ ] **Step 2:** She opens `http://localhost:5173/#/runs` and checks: #17676 (a pass: verify PASS, both diffs present), a `test_fail` run such as #15018 r0, and an `understand`-failed or `understand_miss` run such as #17698 (understand card red or the miss visible, later stages not reached, "no patch").
- [ ] **Step 3:** She checks the Review Focus switch: start a live run on Triage, go to Runs and back; the run and typed text are still there.
- [ ] **Step 4:** Ask her whether to open a PR or push master directly (see memory: ask per phase).
