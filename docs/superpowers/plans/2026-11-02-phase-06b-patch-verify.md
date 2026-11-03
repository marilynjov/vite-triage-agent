# Phase 06b Patch and Verify Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** A patch stage that turns a stored 06a hypothesis into edits against the pre-fix code, and a Docker verifier that runs the real fix's own unit tests on those edits (fail-to-pass).

**Architecture:** The agent tools gain a revision override so the patch stage reads the 2023 pre-fix commit through the existing loop. A per-issue base checkout is installed once in a Docker volume; each verify resets it, writes the edited files and the gold tests, and runs vitest with no network. A harness first proves, per issue, that the gold tests fail before the fix and pass with it; only those issues are paid for.

**Tech Stack:** Python 3.12, pytest, anthropic SDK (existing loop), git, Docker (`node:18-bookworm`, corepack, pnpm 8, vitest 0.34).

**Spec:** `docs/superpowers/specs/2026-11-02-phase-06b-patch-verify-design.md`

## Global Constraints

- Rows: val issues whose fix added/modified a file under `packages/vite/src/**/__tests__/` (43 of 206). Never the test split.
- One patch attempt per run, graded blind: no gold-test output ever reaches a prompt.
- Patch edits: 1 to 5 `{path, old, new}`; paths under `packages/vite/src`, never test files (`/__tests__/`, `.spec.`, `.test.`); each `old` occurs exactly once; edits to one file do not overlap. Violations reject, they do not prune.
- Patch stage: tools `read_file`, `grep`, `list_dir` at the pre-fix commit; turn cap 6.
- Test runs: `--network none`, `--cpus 4`, `--memory 4g`, 300 s timeout; one verify at a time.
- Spend caps (lifetime per file): `val-unit-staged-r1.jsonl` ≤ $0.75, `val-n40-patch.jsonl` ≤ $0.55.
- Stop before any spend if fewer than 15 issues pass the harness.
- Outcomes, exactly one per run: `stale_path`, `patch_error`, `test_fail`, `pass` (and `harness_invalid` per issue, reported, never counted).
- Commits carry no Co-Authored-By or other attribution lines.

## Review Focus

- An edit whose `old` text carries `read_file`'s `"123: "` line-number prefixes: the check must reject it as "not found", not apply garbage (test in Task 2).
- A hypothesis whose `fault.path` exists pre-fix but whose edits target a different, stale file: the check rejects that edit, outcome `patch_error` (Task 2).
- A fix that deletes a source file: the harness pass check must delete it too, not leave the pre-fix file (Task 4).
- A verify left dirty by a crashed or timed-out run: the next verify of that issue must start from the clean base (Task 4).
- An `api`-kind error record (rate limit, 5xx): resume must retry it, and it must not count as `patch_error` in the report (Tasks 6, 7).

---

### Task 1: Tools read at a revision

**Files:**
- Modify: `agent/tools.py`
- Test: `tests/test_tools.py`

**Interfaces:**
- Produces: `tools.at_rev(sha: str) -> ContextManager[None]`; `tools.current_rev() -> str` (the override, else `pinned_sha()`). Every read (`_kind`, `list_dir`, `read_file`, `grep`, `git_log`, `_tracked`, `file_length`) uses `current_rev()`. `_tracked` caches per revision (`lru_cache` on a `_tracked_at(rev)` helper).

- [ ] **Step 1: Write the failing tests**

```python
OLD = "72f6a52^"  # the commit before #14515's fix, July 2023

def _old():
    return tools._git("rev-parse", OLD).stdout.strip()

def test_reads_default_to_the_pin():
    assert tools.current_rev() == tools.pinned_sha()

def test_at_rev_reads_the_older_tree_and_restores():
    old = _old()
    with tools.at_rev(old):
        assert tools.current_rev() == old
        assert tools.read_file("packages/vite/src/node/optimizer/rolldownDepPlugin.ts").startswith("Error: no such file")
        assert not tools.read_file(CSS).startswith("Error: ")
    assert tools.current_rev() == tools.pinned_sha()
    assert not tools.read_file("packages/vite/src/node/optimizer/rolldownDepPlugin.ts").startswith("Error: ")

def test_file_length_follows_the_revision():
    with tools.at_rev(_old()):
        assert tools.file_length(CSS) != 3685
```

- [ ] **Step 2: Run** `pytest tests/test_tools.py -q` — Expected: the three new tests FAIL (`at_rev` not defined).
- [ ] **Step 3: Implement** with a module-level `contextvars.ContextVar[str | None]`; `at_rev` sets and resets it in `try/finally`. Replace each `pinned_sha()` in read paths with `current_rev()`. Keep the module docstring accurate (one sentence on `at_rev`).
- [ ] **Step 4: Run** `pytest -q` — Expected: all PASS (existing callers unchanged).
- [ ] **Step 5: Commit** `git commit -am "Tools: read at a revision with at_rev"`

---

### Task 2: Patch contract, checks and diff

**Files:**
- Modify: `api/schema.py` (add `Edit`, `PatchResult`; `PATCH_MAX_EDITS = 5`)
- Modify: `agent/stages.py` (add `PATCH_SYSTEM`, `PATCH_TOOLS`, `patch_prompt`, `check_patch`)
- Create: `agent/patch.py` (add `apply_edits`, `patch_to_diff`)
- Test: `tests/test_patch.py`

**Interfaces:**
- Consumes: `tools.at_rev`, `tools.current_rev` (Task 1).
- Produces:
  - `Edit(path: str, old: str, new: str)`; `PatchResult(reasoning: str, edits: list[Edit])` with 1..5 items via `json_schema_extra={"maxItems": PATCH_MAX_EDITS}` like `UnderstandResult`; `extra="forbid"`.
  - `stages.check_patch(rev: str) -> Callable[[PatchResult], None]`, raising `ValueError("patch: <reason>")`.
  - `stages.patch_prompt(title: str, body: str, hypothesis: dict) -> str`.
  - `PATCH_TOOLS` = `TOOL_SPECS` entries named `read_file`, `grep`, `list_dir`.
  - `patch.apply_edits(edits: list[Edit], rev: str) -> dict[str, str]` (path → new full content; assumes checked edits).
  - `patch.patch_to_diff(edits: list[Edit], rev: str) -> str` (unified diff, `a/`/`b/` headers, via `difflib.unified_diff`).

- [ ] **Step 1: Write the failing tests** (real clone, at `OLD = 72f6a52^`, file `CSS`):
  - `test_check_patch_accepts_a_real_edit`: `old` = one line copied from the file at OLD, `new` = it plus a comment → no raise.
  - `test_check_patch_rejects_<case>`, each asserting `pytest.raises(ValueError, match="patch: ")`: `path_absent` (`rolldownDepPlugin.ts`), `outside_src` (`packages/vite/package.json`), `test_file` (`packages/vite/src/node/__tests__/plugins/css.spec.ts`), `old_missing`, `old_repeated` (a string occurring twice, e.g. `"import "`), `overlapping_edits` (two edits whose `old` ranges intersect in the same file), `line_number_prefix` (`old` = `"12: " + line12`).
  - `test_patch_result_caps_edits_at_5`: 6 edits fail validation.
  - `test_patch_to_diff_applies_at_rev`: extract `git archive OLD packages/vite/src/node/plugins/css.ts` into `tmp_path`, write the diff there, `git apply --check` in `tmp_path` → exit 0.
  - `test_patch_prompt_says_lines_are_from_newer_code`: the prompt contains the hypothesis JSON and the phrase `"line numbers come from newer code"`.
- [ ] **Step 2: Run** `pytest tests/test_patch.py -q` — Expected: FAIL (names undefined).
- [ ] **Step 3: Implement.** `check_patch` reads files with `git show <rev>:<path>` (through `tools._git`), counts `old` occurrences with `str.count`, and computes each edit's `[start, start+len(old))` span to detect overlap. `PATCH_SYSTEM` follows the spec's prompt line: smallest change at the given fault; read before editing; copy `old` exactly from `read_file` output **without the `N: ` prefixes**; never edit tests; keep the existing `_EVIDENCE` rules.
- [ ] **Step 4: Run** `pytest tests/test_patch.py tests/test_stage_contracts.py -q` — Expected: PASS.
- [ ] **Step 5: Commit** `git commit -am "Patch stage contract: edits, checks, diff"`

---

### Task 3: Run the patch stage through the loop

**Files:**
- Modify: `agent/patch.py` (add `run_patch`)
- Test: `tests/test_patch.py` (FakeClient, copied from `tests/test_pipeline.py`)

**Interfaces:**
- Consumes: Task 2's names; `loop.run`, `loop.Budget`, `loop.RunError`.
- Produces: `patch.run_patch(title: str, body: str, hypothesis: dict, prefix: str, cutoff: str, *, budget: Budget | None = None, client=None) -> Run` — `loop.run(title, body, [], cutoff, system=PATCH_SYSTEM, tool_specs=PATCH_TOOLS, max_turns=6, output_model=PatchResult, check=check_patch(prefix), prompt=patch_prompt(...), budget=..., client=...)` inside `tools.at_rev(prefix)`. `PATCH_TURNS = 6`.

- [ ] **Step 1: Write the failing tests:**
  - `test_run_patch_sends_only_its_three_tools`: `{t["name"] for t in client.calls[0]["tools"]} == {"read_file", "grep", "list_dir"}`.
  - `test_run_patch_tools_read_the_prefix`: a FakeClient turn calls `read_file` on `rolldownDepPlugin.ts`; the `tool_result` sent next contains `"no such file"`.
  - `test_run_patch_caps_turns_at_6`: a FakeClient that always asks for a tool → `RunError`, and `len(client.calls) == 6`.
  - `test_run_patch_bad_edit_raises_run_error`: final text with `old` not in the file → `RunError` whose message contains `"patch: "`.
- [ ] **Step 2: Run** `pytest tests/test_patch.py -q` — Expected: new tests FAIL.
- [ ] **Step 3: Implement** `run_patch`.
- [ ] **Step 4: Run** `pytest -q` — Expected: PASS.
- [ ] **Step 5: Commit** `git commit -am "Patch stage: one loop call at the pre-fix commit"`

---

### Task 4: Fix lookup, the container, and verify

**Files:**
- Create: `eval/fixes.py`, `eval/container.py`, `eval/verify.py`, `eval/docker/Dockerfile`, `tests/conftest.py`
- Modify: `.gitignore` (add `data/verify_*.txt`)
- Test: `tests/test_fixes.py`, `tests/test_verify_docker.py`

**Interfaces:**
- Consumes: `eval.gold_regions.fix_commits`; `patch.apply_edits` (Task 2).
- Produces:
  - `fixes.Fix` (frozen dataclass): `issue: int, fix_sha: str, prefix_sha: str, gold_tests: tuple[str, ...], test_paths: tuple[str, ...], fix_source: tuple[str, ...], fix_deleted: tuple[str, ...]`.
  - `fixes.fix_for(row: dict, commits: list[dict]) -> Fix | None` — the first fix commit (by `fix_commits` order) that touches `packages/vite/src/**/__tests__/`; `prefix_sha` = its first parent; `git diff --name-status prefix fix` gives `A`/`M` under `__tests__/` → `gold_tests`, `*.spec.[tj]s`/`*.test.[tj]s` among them → `test_paths`, other `A`/`M` under `packages/vite/src` → `fix_source`, `D` there → `fix_deleted`. `None` when no unit test changed.
  - `fixes.file_at(sha: str, path: str) -> str`.
  - `container.ensure_image() -> None` (builds `IMAGE = "vite-triage-verify:node18"` from `eval/docker/Dockerfile` if absent).
  - `container.prepare(fix: Fix) -> tuple[bool, str]` (ok, output tail).
  - `container.run_tests(fix: Fix, files: dict[str, str], delete: tuple[str, ...] = (), timeout: int = 300) -> TestRun`; `TestRun(exit_code: int, seconds: float, tail: str, timed_out: bool)`.
  - `verify.VerifyResult(passed: bool, exit_code: int, seconds: float, tail: str, timed_out: bool)`; `verify.verify(fix: Fix, edits: list[Edit]) -> VerifyResult` = `run_tests(fix, {**apply_edits(edits, fix.prefix_sha), **gold test contents at fix_sha})`.

**Container layout** (decided here; it replaces the spec's "copy of the base" with a reset of it, which avoids copying node_modules per run — update the spec line in this task):
- Volumes `vtr-work` (per-issue bases at `/work/<issue>`) and `vtr-store` (pnpm store at `/store/pnpm`, corepack cache at `/store/corepack`, `COREPACK_HOME=/store/corepack`). `repos/vite` mounted read-only at `/repo`.
- `prepare` (network on, skipped if `/work/<issue>/.ready` exists): `git -C /repo archive <prefix> | tar -x`, `git init` + commit everything (so the base is resettable), `pnpm install --frozen-lockfile --store-dir /store/pnpm`, `touch .ready`. A failed install removes the directory.
- `run_tests` (`--network none --cpus 4 --memory 4g`): tar of `files` on stdin; script = `git reset --hard -q && git clean -fdq` (no `-x`: ignored `node_modules` stay) → delete `delete` → `tar -x` → `timeout <timeout> pnpm exec vitest run <fix.test_paths>`. Resetting at the **start** is what keeps a crashed run from leaking into the next. Callers hold `container.LOCK` (a `threading.Lock`) around it.

- [ ] **Step 1: Write the failing tests.**
  - `tests/test_fixes.py` (real clone): `fix_for` on #14515 gives `fix_sha` starting `72f6a52`, `test_paths == ("packages/vite/src/node/__tests__/plugins/css.spec.ts",)`, `fix_source == ("packages/vite/src/node/plugins/css.ts",)`; on #13907 → `None`; `fix_for` puts every `D` path under `packages/vite/src` in `fix_deleted` (use the first of the 43 with a deletion; `pytest.skip` with a reason if none has one).
  - `tests/conftest.py`: register marker `docker`; skip marked tests unless `RUN_DOCKER=1`.
  - `tests/test_verify_docker.py` (`@pytest.mark.docker`): for #14515, `verify(fix, [])` → `passed is False`; `run_tests` with the fix's own source at `fix_sha` plus gold tests → `exit_code == 0`; a second empty verify after that still fails (the reset works); a `run_tests` with `timeout=1` → `timed_out is True`, and the following run is unaffected.
- [ ] **Step 2: Run** `pytest tests/test_fixes.py -q` (FAIL, undefined) and `RUN_DOCKER=1 pytest tests/test_verify_docker.py -q` (FAIL).
- [ ] **Step 3: Implement** the four modules and the Dockerfile (`FROM node:18-bookworm`, `RUN corepack enable`, `WORKDIR /work`). All `docker` invocations go through one `_docker(*args, input=None, timeout=...)` helper using `subprocess.run` with list args.
- [ ] **Step 4: Run** `pytest -q` (docker tests skipped) and `RUN_DOCKER=1 pytest tests/test_verify_docker.py -q` — Expected: PASS. If the 2023 install fails here, stop: that is the harness risk, and the user decides.
- [ ] **Step 5: Edit the spec's** "A copy of the base is made with `cp -a --reflink=auto`…" sentence to describe the reset, then commit `git add -A eval tests .gitignore docs && git commit -m "Verify: fix lookup, Docker base per issue, fail-to-pass test run"`

---

### Task 5: Harness validation over the 43 issues

**Files:**
- Create: `eval/verify_harness.py`
- Test: `tests/test_verify_harness.py`

**Interfaces:**
- Consumes: Task 4's `fix_for`, `prepare`, `run_tests`, `LOCK`.
- Produces:
  - `verify_harness.validate(fix: Fix, *, prepare_fn=prepare, run_fn=run_tests) -> dict` → `{"issue", "valid": bool, "reason": None | "install_failed" | "passes_without_fix" | "fails_with_fix" | "timeout", "prefix_sha", "fix_sha", "fail_tail", "pass_tail"}`.
  - `verify_harness.load_harness() -> dict[int, dict]` from `data/verify_harness.jsonl`.
  - `python -m eval.verify_harness [--issues 14515,...]`: for each unit-tested val row not already cached, validate and append; print a table (issue, valid, reason, seconds); write `data/verify_valid.txt` (all valid ids) and `data/verify_valid_new.txt` (valid ids not in `ablation.val_rows("val", 40)`), one id per line.

- [ ] **Step 1: Write the failing tests** with fake `prepare_fn`/`run_fn`: install fails → `install_failed`; fail-check exit 0 → `passes_without_fix`; pass-check nonzero → `fails_with_fix`; either check `timed_out` → `timeout`; fail nonzero then pass zero → `valid`. Assert the pass-check call received `delete == fix.fix_deleted` and files containing every `fix_source` path. One test: cached issues are not re-validated.
- [ ] **Step 2: Run** `pytest tests/test_verify_harness.py -q` — Expected: FAIL.
- [ ] **Step 3: Implement.**
- [ ] **Step 4: Run** `pytest -q` — Expected: PASS.
- [ ] **Step 5: Commit** `git commit -am "Harness: prove each issue's tests fail before the fix and pass with it"`

---

### Task 6: Staged runs for new issues, and the patch runner

**Files:**
- Modify: `eval/ablation.py` (`--issues PATH`; `val_rows(split, n, issues: set[int] | None = None)`)
- Create: `eval/patch_eval.py`
- Test: `tests/test_ablation.py`, `tests/test_patch_eval.py`

**Interfaces:**
- Consumes: `ablation.load_records`, `ablation.val_rows`, `ablation.RUNS_DIR`; `run_patch` (Task 3); `verify`, `fix_for` (Task 4); `load_harness` (Task 5).
- Produces:
  - `val_rows(split, n, issues=None)`: with `issues`, the rows with those ids sorted by issue, `n` ignored.
  - `patch_eval.STAGED_FILES = (RUNS_DIR / "val-n40-r3.jsonl", RUNS_DIR / "val-unit-staged-r1.jsonl")`; `OUT = RUNS_DIR / "val-n40-patch.jsonl"`.
  - `patch_eval.PatchJob(NamedTuple)`: `issue, repeat, row, source: dict, fix: Fix`.
  - `patch_eval.eligible(staged: list[dict], harness: dict[int, dict], rows: dict[int, dict], fixes: dict[int, Fix]) -> list[PatchJob]` — arm `staged`, `stages.hypothesis` present, harness-valid; raises `ValueError` on a duplicate `(issue, repeat)`.
  - `patch_eval.make_patch_record(job: PatchJob, *, patch_fn=run_patch, verify_fn=verify) -> dict` with the spec's fields: `issue, repeat, source_outcome, source_fault_hit, prefix_sha, patch, diff, verify, outcome, cost_usd, turns, error, error_kind`. `stale_path` when `fault.path` is absent at `prefix_sha` (no `patch_fn` call, `cost_usd == 0.0`); `patch_error` on `RunError` (keeping the partial run's cost); `error_kind = "api"` for `anthropic.APIError` (outcome `null`); else verify → `pass`/`test_fail`. `cutoff` = `row["created_at"]`.
  - `patch_eval.run_patch_jobs(jobs, path, *, cap_dollars: float, workers: int = 4, patch_fn=..., verify_fn=...) -> dict` — same append/flush/torn-line/cap behaviour as `ablation.run_jobs`; verify calls hold `container.LOCK`.
  - `python -m eval.patch_eval [--issues 1,2] [--max-dollars 0.55] [--workers 4]` — refuses to start when lifetime spend + estimate (`0.01` per pending non-stale job until real records exist, then their mean) exceeds the cap; resume skips done keys except `error_kind == "api"`; exits unless `PROVIDER=anthropic`.

- [ ] **Step 1: Write the failing tests:**
  - `test_ablation.py`: `val_rows("val", 40, {14515, 13907}) ` returns exactly those two, sorted.
  - `test_patch_eval.py` (fakes for `patch_fn`/`verify_fn`, hand-made source records): each outcome (`stale_path` with zero `patch_fn` calls; `patch_error` keeps cost; `api` error has `outcome is None`; `pass`; `test_fail`); `eligible` drops harness-invalid and hypothesis-less records and raises on a duplicate key; resume skips done keys but retries `api`; the cap refuses to start; the verify gets the record's own `fix`.
- [ ] **Step 2: Run** `pytest tests/test_ablation.py tests/test_patch_eval.py -q` — Expected: new tests FAIL.
- [ ] **Step 3: Implement.**
- [ ] **Step 4: Run** `pytest -q` — Expected: PASS.
- [ ] **Step 5: Commit** `git commit -am "Patch runner over stored hypotheses; ablation --issues"`

---

### Task 7: The report

**Files:**
- Create: `eval/patch_report.py`
- Test: `tests/test_patch_report.py`

**Interfaces:**
- Consumes: `ablation.load_records`-style reading of `OUT`; `load_harness`.
- Produces: `patch_report.summarize(records: list[dict], harness: dict[int, dict]) -> dict` with keys `outcomes` (`{outcome: {"n", "share", "min", "max"}}`, shares over records with an outcome; min–max of the share across repeats), `patchable_pass_rate`, `by_source_outcome` (`{source_outcome: {"n", "pass"}}`), `by_fault_hit` (among source `hit`s: `{True|False|None: {"n", "pass"}}`), `cost_per_patch`, `cost_per_pass` (`None` with zero passes), `harness_invalid` (`[(issue, reason)]`), `api_errors` (count, excluded from shares). `python -m eval.patch_report [PATH]` prints them as plain tables like `eval/ablation_report.py`.

- [ ] **Step 1: Write the failing tests** on hand-made records: shares and counts; `patchable_pass_rate` excludes `stale_path`; `by_source_outcome` and `by_fault_hit` split; `api` records excluded and counted; `cost_per_pass is None` with no passes; harness-invalid issues listed with reasons.
- [ ] **Step 2: Run** `pytest tests/test_patch_report.py -q` — Expected: FAIL.
- [ ] **Step 3: Implement.**
- [ ] **Step 4: Run** `pytest -q` — Expected: PASS.
- [ ] **Step 5: Commit** `git commit -am "Patch report: outcomes, pass rate by source outcome, cost per pass"`

---

### Task 8: Run it, and write it up

Paid steps; each has a stop condition. Report numbers to the user before step 4.

- [ ] **Step 1: Harness (free).** `python -m eval.verify_harness` — Expected: a table for all 43. **Stop and ask the user if fewer than 15 are valid.**
- [ ] **Step 2: Smoke (≈ $0.06).** `PROVIDER=anthropic python -m eval.ablation --arms staged --issues <first 2 ids of data/verify_valid_new.txt> --repeats 1 --name val-unit-staged-r1 --max-dollars 0.75`, then `PROVIDER=anthropic python -m eval.patch_eval --issues <2 valid ids with stored hypotheses> --max-dollars 0.55`. Read every record by hand: diff sane, verify tail sane.
- [ ] **Step 3: Report the smoke's cost per staged run and per patch to the user,** with the projected total against the $1.30 cap; continue on their yes.
- [ ] **Step 4: Full run.** The ablation command with `--issues data/verify_valid_new.txt`, then `PROVIDER=anthropic python -m eval.patch_eval --max-dollars 0.55`. Expected: no pending jobs on a rerun (resume works).
- [ ] **Step 5: Report.** `python -m eval.patch_report` — read it, then write the docs:
  - `MEASUREMENTS.md` (append only): "Phase 06b" — harness validity (n valid of 43, reasons), the outcome table, the pass rate by source outcome and by `fault_hit`, cost per pass, the one-sentence answer to "does correct localisation turn into a working fix, and how often does a wrong one pass anyway?", and what ~45 runs cannot separate.
  - `ARCHITECTURE.md`: new §10 "Patch and verify" (revision override, patch contract, Docker base and reset, harness, outcomes); update "Where the next phases attach" (Piece B done, Piece C next).
  - `README.md`: the Phase 06 row.
- [ ] **Step 6: Verify and commit.** `pytest -q` PASS, `RUN_DOCKER=1 pytest tests/test_verify_docker.py -q` PASS; `git add MEASUREMENTS.md ARCHITECTURE.md README.md && git commit -m "Measure Phase 06b: patch and verify"`
