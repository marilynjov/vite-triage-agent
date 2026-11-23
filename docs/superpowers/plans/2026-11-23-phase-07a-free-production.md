# Phase 07a Free Production Work Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Do every Phase 07 item that costs $0 of API credit: tighten the test-container sandbox, add a nightly retrieval eval, write a hand-labelled failure taxonomy, add an eval dashboard at `#/eval`, and start the whole app with one command.

**Architecture:** Each piece reuses code that already exists. The sandbox changes only the `docker run` arguments and scripts in `eval/container.py`. The nightly eval wraps `eval/retrieve.py`'s scoring in a function and appends one record per night to a JSONL history. The taxonomy samples failures from a stored run file and keeps hand-written labels in `docs/`. The dashboard is one GET route that reads the existing run files through the existing report functions, plus one React view. Startup is one Python script that checks prerequisites and supervises uvicorn and Vite.

**Tech Stack:** Python 3.12, FastAPI, Pydantic, pytest, Docker 28 (`volume-subpath` needs Engine ≥ 26); React 19, TypeScript, Vitest 4.1.12; macOS launchd for the nightly schedule.

**Spec:** none. Phase 07 has no design doc. The scope comes from the roadmap's Phase 07 line and from `ARCHITECTURE.md` "Where the next phases attach". The decisions below take its place, so rulings made during execution are provisional and should go in the ledger.

### Scope and decisions

Phase 07 has two parts. **07a (this plan) costs nothing.** **07b needs API credit** (about $0.07 is left) and gets its own plan once credit is topped up. 07b covers prompt caching verified through `cache_read_input_tokens`, the cheaper-model rerun, and the single opening of the test split. The test split goes last, after everything else is frozen.

| Decision | Choice |
|---|---|
| Sandbox | Test runs mount only `/work/<issue>` (a `volume-subpath` mount), with no `/store` and no `/repo`. They run as uid `1000:1000` (`node` in `node:18`) with `--pids-limit 512`, `--cap-drop ALL` and `--security-opt no-new-privileges`, plus the existing network none, 4 CPUs, 4 GB and 300 s. Without `/store` there is no corepack cache, so tests run `./node_modules/.bin/vitest` directly instead of through `pnpm exec`. `prepare` stays root (it needs `/store`) and chowns the base to `1000:1000`. |
| Nightly eval | Retrieval only ($0): strategy `fixed_v1_header` over all val rows. One record per run goes to `data/nightly/retrieve.jsonl`. Retrieval is deterministic, so any change in a metric from the previous ok record with the same pin, strategy and n counts as a change. Exit codes: 0 same or first record, 1 changed, 2 failed (the failure is recorded too). It is scheduled by a launchd plist that she installs herself; this plan never writes to `~/Library`. |
| Taxonomy | 20 failures sampled with `random.Random(0)` from the clean failures (`recall_at_1 == 0`, not api errors) of the `agent` arm in `data/runs/val-n206-r1.jsonl`, the chosen arm on all 206 val rows (67 to sample from). Labels live in `docs/failure-taxonomy.jsonl` and categories in `docs/failure-taxonomy.md`, both committed. The executor drafts the labels from evidence and marks them as a draft for her review. |
| Dashboard | `GET /eval/summary` covers the nightly history, arm scores (clean) from `val-n206-r1`, staged outcomes (clean) from `val-n40-r3`, patch outcomes from the patch file, and taxonomy counts. A missing or empty source gives an empty section, never a 500. The view at `#/eval` is plain tables with no chart library. |
| Startup | `.venv/bin/python scripts/dev.py` runs the preflight checks, then `docker compose up -d --wait`, then uvicorn (`--reload`, port 8000) and `npm --prefix web run dev`. When either process exits or Ctrl-C is pressed, the other is stopped. `PROVIDER` comes from `.env` as before. |

## Global Constraints

- $0: no Anthropic call anywhere in 07a. Ollama embeddings (local) are allowed.
- Never read `data/splits/test*`. The nightly eval and the taxonomy read only the val split and val run files.
- No new pip or npm dependencies, no chart library, no router library.
- Types reach the UI only through `scripts/gen_types.py`; never hand-edit `web/src/types.generated.ts`.
- The Triage and Runs views behave as they do now.
- Check `df -h /` before any Docker step that prepares a base (2–3 GB each). Stop below 10 GB free. Never delete Docker caches or volumes without asking.
- Commits carry no Co-Authored-By or other attribution lines.
- Use `.venv/bin/python` / `.venv/bin/pytest`; `npm --prefix web …` for the frontend.

## Review Focus

- A base prepared before this change (root-owned) and reused because `.ready` exists: the non-root test run must still be able to `git reset` it, so the ready-exit branch also chowns (Task 1 test).
- The nightly run while Postgres or Ollama is down (a 3 a.m. launchd run): it records `ok: false` with the error and exits 2. A failed record is never compared as a baseline (Task 2 test).
- A nightly record with a different pin or strategy from the last one: it is a new baseline, not a "change" (Task 2 test).
- `GET /eval/summary` with no nightly file, no taxonomy labels, or an empty run file: empty sections and 200 (Task 4 test).
- `#/eval` alongside `#/runs/…`: the router handles three views, and unknown hashes still fall back to Triage (Task 5 test).

---

### Task 1: Sandbox the test container

**Files:**
- Modify: `eval/container.py` (`_prepare_script`, `run_tests`; new `_test_args`)
- Test: `tests/test_container.py`, `tests/test_verify_docker.py`

**Interfaces:**
- Produces: `TEST_USER = "1000:1000"`, `PIDS_LIMIT = 512`, `_test_args(fix: Fix, name: str) -> list[str]` (everything from `run` through `IMAGE`), `_prepare_script(..., owner: str = TEST_USER)`.

- [ ] **Step 1: Write the failing unit tests** in `tests/test_container.py`

```python
def test_test_args_mount_only_the_issue_base_as_non_root():
    args = container._test_args(_fix(), "vtr-test-1-x")
    joined = " ".join(args)
    assert "type=volume,src=vtr-work,dst=/work/1,volume-subpath=1" in args
    assert "vtr-store" not in joined and ":/repo" not in joined
    for flag, value in [("--user", "1000:1000"), ("--pids-limit", "512"), ("--cap-drop", "ALL"),
                        ("--security-opt", "no-new-privileges"), ("--network", "none"),
                        ("--cpus", "4"), ("--memory", "4g")]:
        assert args[args.index(flag) + 1] == value
    assert args[-1] == container.IMAGE and "--rm" not in args

def test_prepare_hands_the_base_to_the_test_user_even_when_already_ready(tmp_path):
    me = f"{os.getuid()}:{os.getgid()}"
    script = container._prepare_script(_fix(), work=str(tmp_path), repo=str(M.REPO_PATH),
                                       install="true", build="true", owner=me)
    assert script.count(f'chown -R {me} "$d"') == 2  # ready-exit branch and before .ready
    assert subprocess.run(["sh", "-c", script], capture_output=True, timeout=300).returncode == 0
```

Pass `owner=f"{os.getuid()}:{os.getgid()}"` in the existing `_run` helper too, because a non-root host can't chown to 1000. Also add a test that the `run_tests` script runs `./node_modules/.bin/vitest run` and contains no `pnpm exec`. Capture the script by monkeypatching `container._docker` to record its arguments and return a `CompletedProcess(args, 1, b" Test Files  1 failed (1)\n")`.

- [ ] **Step 2: Run them to verify they fail**

Run: `.venv/bin/pytest tests/test_container.py -q`
Expected: FAIL. `_test_args` doesn't exist, `owner` is an unexpected keyword, and the script still says `pnpm exec`.

- [ ] **Step 3: Implement.** Add `_test_args`, which uses the `--mount` string above, the env vars `CI=1` and `NO_COLOR=1`, `-i`, and no `COREPACK_*` variables. `run_tests` calls it. `_prepare_script` gains `owner` and runs `chown -R {owner} "$d" || fail chown` in the ready-exit branch (before `exit 0`) and just before `touch .ready`. In the ready-exit branch, a failed chown prints and exits 1 without removing the base. The test command becomes `./node_modules/.bin/vitest run {tests}`. Update the module docstring: test runs see only their base, as uid 1000.

- [ ] **Step 4: Run the unit tests to verify they pass**

Run: `.venv/bin/pytest tests/test_container.py tests/test_verify_harness.py tests/test_patch_eval.py -q`
Expected: PASS

- [ ] **Step 5: Add the Docker test** in `tests/test_verify_docker.py`, using its existing `fix` fixture and its prepared base:

```python
def test_the_test_container_sees_only_its_base_as_uid_1000(fix):
    r = container._docker(*container._test_args(fix, container._name(fix, "probe")), "sh", "-c",
                          "id -u; ls /work; test ! -e /store && test ! -e /repo && echo isolated; "
                          f"touch /work/{fix.issue}/.probe && echo writable")
    out = container._text(r)
    assert out.split()[:2] == ["1000", str(fix.issue)] and "isolated" in out and "writable" in out
```

Then remove the `.probe` file the same way, or rely on `run_tests` running `git clean -fdq`.

- [ ] **Step 6: Run the Docker tests.** Check `df -h /` first (stop below 10 GB free).

Run: `.venv/bin/pytest -m docker tests/test_verify_docker.py -q`
Expected: all pass, including fail→pass→fail and timeout, now as uid 1000 with no `/store`. If vitest can't start without corepack, that is a plan defect: put the ruling in the ledger and don't add `/store` back.

- [ ] **Step 7: Run the harness on one real valid issue.** Check `df` first.

Run: `.venv/bin/python -c "from eval import fixes, verify_harness as h, container as c; f=fixes.fix_for(next(r for r in h.candidate_rows() if r['issue']==17676), h.load_commits()); print(h.validate(f)); c.discard(f)"`
Expected: a dict with `valid: True`. This is the same verdict as before the sandbox; the gold tests still fail without the fix and pass with it. `validate` doesn't append to `data/verify_harness.jsonl`. If its signature differs, match it and record that in the ledger.

- [ ] **Step 8: Commit**

```bash
git add eval/container.py tests/test_container.py tests/test_verify_docker.py
git commit -m "Sandbox test runs: only the issue's base, uid 1000, no store, pids and caps limited"
```

### Task 2: Nightly retrieval eval

**Files:**
- Modify: `eval/retrieve.py` (extract `evaluate`; `main` prints from it)
- Create: `eval/nightly.py`, `scripts/nightly.sh`, `scripts/com.vitetriage.nightly.plist`
- Modify: `.gitignore` (add `data/nightly/`)
- Test: `tests/test_nightly.py`

**Interfaces:**
- Produces: `retrieve.evaluate(rows: list[dict], k_values: list[int], strategy: str) -> dict` returns `{"n", "clean_n", "seconds", "all": {metric: float}, "clean": {metric: float}}` with metrics `recall@1`, `recall@5`, `recall@10`, `mrr`.
- Produces: `nightly.HISTORY = M.DATA / "nightly" / "retrieve.jsonl"`, `nightly.STRATEGY = "fixed_v1_header"`, `nightly.load_history(path) -> list[dict]`, `nightly.compare(prev: dict | None, cur: dict) -> bool` (True means changed), `nightly.run(*, evaluate=retrieve.evaluate, path=HISTORY, now=None) -> int` (the exit code).
- A record is `{"at": ISO-8601 UTC, "ok": bool, "pin": str, "strategy": str, "n": int, "clean_n": int, "all": {...}, "clean": {...}, "seconds": float, "changed": bool, "error": str | None}`. Failed records have `all`/`clean` = `{}`.

- [ ] **Step 1: Write the failing tests** (`evaluate` is injected; no DB and no Ollama)

```python
def fake(rows, k_values, strategy, value=0.5):
    m = {"recall@1": value, "recall@5": value, "recall@10": value, "mrr": value}
    return {"n": len(rows), "clean_n": len(rows), "seconds": 1.0, "all": m, "clean": m}

def test_first_record_is_a_baseline_and_exits_0(tmp_path): ...       # ok True, changed False, rc 0
def test_same_numbers_exit_0_and_a_moved_metric_exits_1(tmp_path): ...  # second run value 0.51 -> changed True, rc 1
def test_a_failure_is_recorded_exits_2_and_is_never_a_baseline(tmp_path): ...
    # evaluate raises RuntimeError("db down") -> record ok False, error "db down", rc 2;
    # a following run with value 0.5 compares against the earlier ok record, not the failure
def test_a_new_pin_or_strategy_is_a_new_baseline_not_a_change(): ...  # compare(prev with other pin, cur) is False
def test_nightly_reads_only_the_val_split(tmp_path, monkeypatch): ...  # spy builtins.open/Path.read_text; no path contains "test"
```

Write each body out in full with these assertions. Read records back with `load_history`.

- [ ] **Step 2: Run them to verify they fail**

Run: `.venv/bin/pytest tests/test_nightly.py -q`
Expected: FAIL with `ModuleNotFoundError: eval.nightly`

- [ ] **Step 3: Implement.** Move the scoring loop out of `retrieve.main` into `evaluate`. `main` keeps its exact printed output. `nightly.run` loads all of `val.jsonl`, reads the pin from `M.PINNED_SHA`, calls `evaluate(rows, [1, 5, 10], STRATEGY)` inside try/except Exception, compares against the last `ok` record with the same pin, strategy and n (any metric differing by more than `1e-9` counts as changed), appends the record (creating the directory), prints one summary line, and returns 0/1/2. `python -m eval.nightly` calls `sys.exit(run())`. `scripts/nightly.sh` changes to the repo directory (its own parent's parent) and runs `.venv/bin/python -m eval.nightly >> data/nightly/nightly.log 2>&1`. The plist runs that script at 03:00 daily (`StartCalendarInterval`). The repo path in the plist is the placeholder `__REPO__`, and its comment gives the `sed` and `launchctl load` commands.

- [ ] **Step 4: Run the tests to verify they pass**

Run: `.venv/bin/pytest tests/test_nightly.py tests/test_scoring.py -q`
Expected: PASS

- [ ] **Step 5: Run it for real twice.** Postgres and Ollama must be up.

Run: `.venv/bin/python -m eval.nightly; echo rc=$?; .venv/bin/python -m eval.nightly; echo rc=$?`
Expected: `rc=0` both times. The second run is not a change. Clean recall@10 for `fixed_v1_header` on all val rows is a fixed number, so check it against `MEASUREMENTS.md` (Phase 03, 206 rows). If the number is new, record it in Task 6.

- [ ] **Step 6: Commit**

```bash
git add eval/retrieve.py eval/nightly.py scripts/nightly.sh scripts/com.vitetriage.nightly.plist .gitignore tests/test_nightly.py
git commit -m "Nightly retrieval eval: one record a night, a moved metric exits 1"
```

### Task 3: Failure taxonomy

**Files:**
- Create: `eval/taxonomy.py`, `docs/failure-taxonomy.md`, `docs/failure-taxonomy.jsonl`
- Modify: `.gitignore` (add `data/taxonomy-sample.md`)
- Test: `tests/test_taxonomy.py`

**Interfaces:**
- Produces: `taxonomy.SOURCE = M.DATA / "runs" / "val-n206-r1.jsonl"`, `taxonomy.LABELS = M.ROOT / "docs" / "failure-taxonomy.jsonl"`, `sample_failures(records: list[dict], n: int = 20, seed: int = 0) -> list[dict]`, `load_labels(path) -> list[dict]` (each `{issue, repeat, category, note}`; missing file gives `[]`; empty category or a duplicate `(issue, repeat)` raises `ValueError`), `counts(labels) -> list[tuple[str, int]]` (n descending, then name).
- CLI: `python -m eval.taxonomy sample` writes `data/taxonomy-sample.md`. For each failure it shows the issue number, title, the first 1,500 characters of the body, gold, picked (top 5), candidates, turns, tool calls and error. `python -m eval.taxonomy report` prints the counts.

- [ ] **Step 1: Write the failing tests:**
  - The sample includes only agent-arm, clean, `recall_at_1 == 0`, non-api records.
  - It is the same 20 every run and sorted by issue.
  - It raises if fewer than `n` failures exist.
  - The `load_labels` rules above hold.
  - `counts` orders `[("b",2),("a",1),("c",1)]`.
- [ ] **Step 2: Run** `.venv/bin/pytest tests/test_taxonomy.py -q`. Expected: FAIL, module missing.
- [ ] **Step 3: Implement** `eval/taxonomy.py`. Sort the eligible records by `(issue, repeat)`, then call `random.Random(seed).sample(eligible, n)`, then sort by issue. Rows come from `val_rows("val", ...)` or `val.jsonl`, never the test split.
- [ ] **Step 4: Run** the tests. Expected: PASS.
- [ ] **Step 5: Label the 20.** Run `.venv/bin/python -m eval.taxonomy sample` and read each failure in `data/taxonomy-sample.md`. Open the gold file at the pin with `git -C repos/vite show <pin>:<path>` where the record alone doesn't explain the miss. Write one line per failure to `docs/failure-taxonomy.jsonl`. `note` is one sentence of evidence, e.g. "gold `css.ts` was candidate 7; agent stopped after 1 turn without a tool call". Categories come out of the reading: at most 6, plus `other`. Each has a name, a one-line definition, its count, and one example issue in `docs/failure-taxonomy.md`. The file opens with "Draft labels by Claude, for Marilyn's review; categories are provisional". Then run `python -m eval.taxonomy report` and paste its output into the doc.
- [ ] **Step 6: Commit**

```bash
git add eval/taxonomy.py tests/test_taxonomy.py docs/failure-taxonomy.md docs/failure-taxonomy.jsonl .gitignore
git commit -m "Failure taxonomy: 20 sampled agent misses, labelled by hand"
```

### Task 4: `GET /eval/summary`

**Files:**
- Modify: `api/schema.py`, `api/main.py`, `scripts/gen_types.py` (add the models to `STAGED_MODELS`'s merge, or a sibling `EVAL_MODELS` list), `web/src/types.generated.ts` (regenerated)
- Create: `api/eval_summary.py`
- Test: `tests/test_eval_summary.py`

**Interfaces:**
- Consumes: `nightly.load_history`, `nightly.HISTORY` (Task 2), `taxonomy.load_labels`, `taxonomy.counts`, `taxonomy.LABELS` (Task 3), `ablation.load_records`, `ablation_report.arm_scores`, `ablation_report.cost_stats`, `ablation_report.outcome_table`, `patch_eval.load_patch_records`, `patch_eval.OUT`, `patch_report.summarize`, `verify_harness.load_harness`.
- Produces (all `extra="forbid"`):
  - `NightlyPoint{at: str, ok: bool, strategy: str, n: int, recall_at_10_clean: float | None, mrr_clean: float | None, changed: bool, error: str | None}`
  - `ArmRow{arm: str, runs: int, recall_at_1: float, recall_at_5: float, mrr: float, per_triage: float | None}`
  - `Share{name: str, n: int | None, share: float}`
  - `TaxonomyCount{category: str, n: int}`
  - `EvalSummary{nightly: list[NightlyPoint], arms_source: str, arms: list[ArmRow], staged_outcomes: list[Share], patch_outcomes: list[Share], patch_pass_rate: float | None, taxonomy: list[TaxonomyCount]}`
  - `eval_summary.ARMS_FILE = M.DATA / "runs" / "val-n206-r1.jsonl"`, `STAGED_FILE = M.DATA / "runs" / "val-n40-r3.jsonl"`, `summary() -> EvalSummary`
  - Route `GET /eval/summary` → `EvalSummary`, plain `def`.
- Arms are listed in `("retrieval", "grounded", "agent")` order, clean subset, with each metric's mean (element 0 of the `arm_scores` tuple). `runs` is the count of clean non-api records. Nightly lists the newest 30 records, oldest first.

- [ ] **Step 1: Write the failing tests.** Monkeypatch every path to `tmp_path` files and test:
  - Fixtures with 2 arms, 3 staged outcomes, 2 patch outcomes, 2 nightly records (one failed) and 2 labels give the expected values.
  - Every source missing gives 200 with empty lists, `patch_pass_rate` None and `arms_source` still the file name.
  - A failed nightly record has `recall_at_10_clean` None.
  - The test-split guard spies on `open` and `read_text`, as in `tests/test_staged_runs.py`.

  Use a `TestClient` with the db connect/disconnect stubbed the way `tests/test_staged_runs.py` does.
- [ ] **Step 2: Run** `.venv/bin/pytest tests/test_eval_summary.py -q`. Expected: FAIL.
- [ ] **Step 3: Implement** the models, `api/eval_summary.py` and the route. Each section is built in its own try/except that logs and returns the empty value, so one bad file empties one section only. Regenerate the types with `.venv/bin/python scripts/gen_types.py`. Add the route to the Routes bullet in `ARCHITECTURE.md`.
- [ ] **Step 4: Run** `.venv/bin/pytest -q` (the whole suite) and `npm --prefix web run build`. Expected: PASS and build OK.
- [ ] **Step 5: Commit**

```bash
git add api/schema.py api/main.py api/eval_summary.py scripts/gen_types.py web/src/types.generated.ts tests/test_eval_summary.py ARCHITECTURE.md
git commit -m "GET /eval/summary: nightly, arms, staged and patch outcomes, taxonomy, from stored files"
```

### Task 5: Eval dashboard view at `#/eval`

**Files:**
- Modify: `web/src/router.ts`, `web/src/router.test.ts`, `web/src/App.tsx` (third header link, render `EvalView`), `web/src/App.css`
- Create: `web/src/evalView.tsx`

**Interfaces:**
- Consumes: `EvalSummary` and friends from `types.generated.ts`, `API` from `api.ts`.
- Produces: `Route` gains `{ view: 'eval' }`; `parseHash('#/eval')` returns it.

- [ ] **Step 1: Write the failing router tests:** `parseHash('#/eval')` → `{view:'eval'}`; `'#/eval/x'` → triage; the existing runs cases still pass.
- [ ] **Step 2: Run** `npm --prefix web test`. Expected: FAIL on the eval cases.
- [ ] **Step 3: Implement.** Router: add the eval route. `EvalView` fetches once on mount and shows "Loading…" until the data arrives, or "Failed to load: <message>" on error. It has five sections, each with a heading and a plain `<table>`; an empty section says "No data yet".
  - **Nightly retrieval:** at, ok, recall@10 clean, MRR clean, changed. Highlight failed and changed rows.
  - **Arms on val (clean):** with the source file name.
  - **Staged outcomes (clean)**
  - **Patch outcomes:** plus the pass rate over patchable runs.
  - **Failure taxonomy**

  Percentages show one decimal place and dollars four. Use the `runs-page` width rule so the page uses wide windows. `App` stays as it is apart from the third link and route.
- [ ] **Step 4: Run** `npm --prefix web test && npm --prefix web run lint && npm --prefix web run build`. Expected: all pass, with no oxlint warnings.
- [ ] **Step 5: Commit**

```bash
git add web/src/router.ts web/src/router.test.ts web/src/App.tsx web/src/App.css web/src/evalView.tsx
git commit -m "Eval dashboard at #/eval: plain tables over /eval/summary"
```

### Task 6: One-command startup, docs, and the manual check

**Files:**
- Create: `scripts/dev.py`, `tests/test_dev.py`
- Modify: `README.md` (Quick start, Measuring, nightly install), `ARCHITECTURE.md` (sandbox paragraph, phase table: 06c done, 07a done, 07b next), `MEASUREMENTS.md` (Phase 07a: first nightly record, sandbox smoke, taxonomy counts)

**Interfaces:**
- Produces: `dev.preflight(*, which=shutil.which, run=subprocess.run, http_get=..., port_free=...) -> list[str]` returns one fix-it line per problem, and `[]` when ready. `dev.supervise(procs: list[Popen], poll: float = 0.5) -> int` waits until any process exits or a KeyboardInterrupt arrives, terminates the rest (then kills each after 10 s), and returns the first exit code (130 on Ctrl-C). `dev.main() -> int`.
- The preflight problems, each with its fix:
  - `.venv/bin/python` is missing: `python3 -m venv .venv && .venv/bin/pip install -r requirements.txt`.
  - `web/node_modules` is missing: `npm --prefix web ci`.
  - `docker info` fails: `open -a Docker`.
  - Ollama at `OLLAMA_HOST/api/tags` is unreachable or lacks `nomic-embed-text`: `ollama serve` / `ollama pull nomic-embed-text`.
  - Port 8000 or 5173 is in use: `lsof -i :<port>`.

- [ ] **Step 1: Write the failing tests:**
  - Each preflight problem yields its line, and all checks passing yields `[]`.
  - `supervise` with one fake process that exits 3 terminates the other and returns 3.
  - A KeyboardInterrupt raised from the poll sleep terminates both and returns 130.

  The fake process is a small class with `poll`, `terminate`, `wait` and `kill`.
- [ ] **Step 2: Run** `.venv/bin/pytest tests/test_dev.py -q`. Expected: FAIL.
- [ ] **Step 3: Implement** `scripts/dev.py`. `main` prints the problems and returns 1 if there are any. Otherwise it runs `docker compose up -d --wait`, starts `.venv/bin/python -m uvicorn api.main:app --port 8000 --reload` and `npm --prefix web run dev`, prints "API http://localhost:8000 · web http://localhost:5173 · Ctrl-C stops both", and returns `supervise(...)`. Use the `if __name__ == "__main__": sys.exit(main())` guard. The tests import it via `importlib` from its path.
- [ ] **Step 4: Run** `.venv/bin/pytest -q && npm --prefix web test`. Expected: PASS.
- [ ] **Step 5: Update the docs.** The README quick start's "Run the app" becomes `.venv/bin/python scripts/dev.py`, with the old two commands kept as the manual way. Add a "Nightly eval" subsection with the plist install commands and the exit-code meanings, and a `#/eval` line. In ARCHITECTURE.md, replace the "Phase 07 should also tighten…" paragraph with what the sandbox now is, and update the phase table. Add a Phase 07a section to MEASUREMENTS.md with the first nightly numbers, the Task 1 smoke verdict, and the taxonomy counts table.
- [ ] **Step 6: Manual check (Marilyn).** Stop any running API first. Run `.venv/bin/python scripts/dev.py`, open `http://localhost:5173/#/eval`, and confirm all five sections show data. Confirm Triage and Runs still work. Ctrl-C should stop both processes, with nothing left on ports 8000 or 5173.
- [ ] **Step 7: Commit**

```bash
git add scripts/dev.py tests/test_dev.py README.md ARCHITECTURE.md MEASUREMENTS.md
git commit -m "One-command startup, and Phase 07a in the docs"
```
