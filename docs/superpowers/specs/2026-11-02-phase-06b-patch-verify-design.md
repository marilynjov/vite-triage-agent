# Phase 06, Piece B — Patch and verify: design

Date: 2026-11-02 · Status: approved in conversation, awaiting spec review

## Purpose

Piece A ends at a hypothesis: a fault location and a mechanism. Piece B asks
whether that hypothesis is good enough to fix the bug. A **patch** stage writes
edits against the code as it was before the fix, and a **verify** step applies
them in a container and runs the tests the real fix added. Those tests fail
without the fix, so a pass shows the patch fixes the reported behaviour rather
than merely not breaking anything (fail-to-pass, the SWE-bench criterion).

The question Piece B answers: **does correct localisation turn into a working
fix, and how often does a wrong one pass anyway?**

## Decisions already made

| Decision | Choice | Why |
|---|---|---|
| What verify proves | Fail-to-pass on the real fix's own unit tests | The only check that shows the bug is fixed. A regression-only check passes a no-op patch. |
| Rows | Every val issue whose fix changed a file under `packages/vite/src/**/__tests__/`: 43 of 206 | Only 9 of the 40-row slice qualify (8 with a stored hypothesis), too few to separate anything. `playground/**/__tests__` are browser e2e tests and do not count. |
| Patch input | 06a staged hypotheses: the stored ones in `val-n40-r3.jsonl`, plus one new staged run (at the pin, unchanged pipeline) for each harness-valid issue outside the slice | Reuses the 06a pipeline as built; re-running it on pre-fix code would need a retrieval index per checkout. |
| Stale hypotheses | Recorded as `stale_path`, no model call | 06a reads the 2026 pin; on the slice, 9 of 41 hypotheses name a file absent before their 2023 fix. The count measures how much the pin warps 06a. |
| Feedback | One attempt, graded blind | The gold tests are the answer key; showing their output to the model would leak it. |
| Where tests run | Docker | 2023 checkouts pin `pnpm@8.6.7` and Node ≤ 20; the host has Node 23. The test run executes model-written code, so it runs with no network. Matches roadmap p6d. |
| Budget | ≤ $0.75 lifetime on the new staged run file, ≤ $0.55 on the patch run file (≤ $1.30 total) | ≈ 34 staged runs at ≈ $0.019 and ≈ 45 patches at ≈ $0.01; only harness-valid issues are paid for; the smoke test measures the real figures. |

## 1. Harness validation (no model calls)

`eval/verify_harness.py`, run once per candidate issue (all 43, before any spend); results cached in
gitignored `data/verify_harness.jsonl`.

1. **Fix commit:** the row's commit in `data/raw/commits.jsonl` (as
   `eval/gold_regions.py` finds it). **Pre-fix commit:** its first parent.
   **Gold tests:** the fix's added or modified files under
   `packages/vite/src/**/__tests__/` (specs, snapshots, fixtures), taken whole
   at the fix commit; the files run are the `*.spec.ts` / `*.test.ts` among them. **Fix source:** the fix's other changed files
   under `packages/vite/src`.
2. **Prepare** (network on): in the container, export the pre-fix commit
   into a per-issue directory under a Docker volume and commit it to a fresh
   git repo (so it can be reset), run `pnpm install --frozen-lockfile` against a
   shared pnpm store volume (corepack enabled), then
   `pnpm --filter ./packages/vite run build-bundle`: vitest loads the workspace's
   `vite` from its `dist`, as Vite's own CI builds before `test-unit`. Any failed
   step removes the directory; `.ready` is written only after all succeed. This
   checkout is the cached base for every later verify of the issue.
3. **Fail check** (network off): the base, reset, + gold tests →
   `pnpm exec vitest run <gold tests>` must exit nonzero with vitest reporting
   failed tests (status `test_fail`, not `infra`).
4. **Pass check** (network off): the base, reset, + gold tests + fix source at
   the fix commit (and the fix's deletions) → the same command must exit zero.

Anything else is `harness_invalid` with a reason (`install_failed`,
`passes_without_fix`, `fails_with_fix`, `timeout`). Those issues are excluded
from the run and listed in the report. The pass check also validates verify
itself: if the real fix does not pass, the harness is wrong, not the model.

**Container:** one image from `node:18` with `git` and the repo mounted
read-only. Every test run uses `--network none`, `--cpus 4`, `--memory 4g` and a
300 s timeout. The base is its own git repo (the pre-fix tree committed once), so
each run starts with `git reset --hard && git clean -fd` (no `-x`: the ignored
`node_modules` and built `dist` stay) and then lays its files over it; the install
is never repeated and no copy is made. Resetting at the start keeps a crashed run
from leaking into the next, and runs on one base are serialised by a lock.

## 2. The patch stage

**Tools at a revision** (`agent/tools.py`): a context variable holds the
revision the tools read, defaulting to `pinned_sha()`. `at_rev(sha)` is a
context manager that sets it. Every existing read (`_kind`, `list_dir`,
`read_file`, `grep`) goes through it, so all current callers are unchanged.
`git_log` keeps its cutoff. At the pre-fix commit, history does not yet contain
the fix, so the patch stage has nothing to leak.

**Models** (`api/schema.py`):
- `Edit`: `path`, `old`, `new`.
- `PatchResult`: `reasoning`, `edits: list[Edit]` (1 to 5 items).

**Stage** (`agent/stages.py`: prompt, tools, check; `agent/patch.py`: one call
through `loop.run` under `at_rev(prefix)`):

| | |
|---|---|
| Input message | issue + the stored `HypothesisResult` (JSON), with a note that its line numbers come from newer code and the file may differ |
| Tools | read_file, grep, list_dir at the pre-fix commit |
| Turn cap | 6 |
| Output | `PatchResult` |
| Check (rejects, does not prune) | each `path` exists at the pre-fix commit, is under `packages/vite/src`, and is not a test file (`/__tests__/`, `.spec.`, `.test.`); each `old` occurs exactly once in that file; edits to one file do not overlap |

**Prompt:** make the smallest change that fixes the reported behaviour at the
fault given; read the code before editing it; copy `old` exactly from what
`read_file` returned, without line-number prefixes; do not edit tests.

`patch_to_diff(edits, rev) -> str` applies the edits to the files at `rev` in
memory and returns a unified `git diff`, stored on the record for Piece C's diff
viewer.

## 3. Verify, the run, the report

**Verify** (`eval/verify.py`): `verify(issue, edits) -> VerifyResult` resets
the issue's base, writes the edited files, copies in the gold tests and
runs the gold tests under the container limits above. It returns `passed`,
`exit_code`, `seconds`, the output tail (last 4 KB) and `status`, one of
`pass`, `test_fail` (vitest ran and reported failed test files), `timeout` or
`infra` (anything else: Docker, reset, OOM, no tests found, missing base);
`infra` is never counted as a test failure.

**Outcome per run**, exactly one:

| Outcome | Condition |
|---|---|
| `harness_invalid` | the issue failed §1; not counted in any rate |
| `stale_path` | the hypothesis's `fault.path` does not exist at the pre-fix commit; no model call |
| `patch_error` | the stage raised (`RunError`, budget stop, failed check) |
| `test_fail` | the edits applied but the gold tests failed or timed out |
| `pass` | the gold tests passed |

**New staged runs:** `eval/ablation.py` gains `--issues <file of ids>`, which
restricts the rows to those ids (still sorted by issue). The harness-valid
issues outside the 40-row slice run once through the `staged` arm into
`data/runs/val-unit-staged-r1.jsonl` with `--max-dollars 0.75`.

**The run** (`eval/patch_eval.py`): reads the arm-`staged` records with a
hypothesis from both staged files, keeps those whose issue passed §1,
and writes one record per source record to `data/runs/val-n40-patch.jsonl`.
Each record carries: `issue`, `repeat`, the source record's `outcome` and
`fault_hit`, `prefix_sha`, `patch` (the `PatchResult` or null), `diff`,
`verify`, `outcome`, `cost_usd`, `turns` and `error`. It resumes on
`(issue, repeat)` and refuses to start past `--max-dollars` (lifetime per file,
default 0.55), like `eval/ablation.py`.

1. Harness over all 43 issues (free). Stop and decide if fewer than 15 are valid.
2. Smoke test: 2 new staged runs, then 2 patches. Measure cost per run and per patch.
3. The new staged runs, then the full patch run.

**The report** (`eval/patch_report.py`):
- the outcome table: count and share of each outcome, with min–max across
  repeats (2 per slice issue, 1 per new issue)
- pass rate over patchable runs (`patch_error` + `test_fail` + `pass`)
- pass rate by the source 06a outcome, and by `fault_hit` among `hit`s
- cost per patch and per pass
- each `harness_invalid` issue with its reason

## 4. Tests (no paid calls)

- **`tests/test_tools.py` additions:** reads default to the pin; `at_rev`
  reads an older revision and restores the pin on exit; a path that exists only
  at the pin is missing under `at_rev(<2023 commit>)`.
- **`tests/test_patch.py`** (FakeClient): the stage sends only its three tools
  and runs under `at_rev(prefix)`; each check rejects (path absent, outside
  `packages/vite/src`, a test file, `old` missing, `old` repeated, overlapping
  edits); `patch_to_diff` on a real 2023 file gives a diff that `git apply
  --check` accepts at that revision.
- **`tests/test_patch_eval.py`:** each outcome from hand-made inputs;
  `stale_path` makes no model call; resume skips done `(issue, repeat)` pairs;
  the spend cap refuses to start.
- **`tests/test_patch_report.py`:** the outcome table, rates and the
  by-source-outcome split on hand-made records.
- **`tests/test_verify_docker.py`** (marker `docker`, skipped by default):
  issue #14515's real fix (`72f6a52`, one source file plus `css.spec.ts`)
  passes verify, and the empty patch fails.
- **`tests/test_ablation.py` addition:** `--issues` restricts the rows.

## 5. Docs

- **`MEASUREMENTS.md`** (append-only), a "Phase 06b" section: harness
  validity, the outcome table, the pass rate by source outcome, cost per pass,
  and the one-sentence answer to the question in Purpose.
- **`ARCHITECTURE.md`:** §10, patch and verify; update "Where the next phases
  attach".
- **`README.md`:** the Phase 06 row.

## Out of scope

- Retries driven by test output (leaks the answer key).
- Re-running locate/understand/hypothesize on the pre-fix code (option B in
  conversation; would need a retrieval index per checkout).
- Playground tests, and the issues whose fixes have no unit tests (163 of 206).
- Running the full existing suite as a regression check.
- The UI (Piece C).

## Risks

- **2023 installs may not reproduce.** A lockfile can name packages that are
  gone or need native builds. §1 runs before any spend and reports how many
  issues survive. If fewer than 15 survive, stop and decide before paying.
- **Some gold tests may not fail without the fix** (the fix changed a test for
  other reasons). §1 excludes them as `passes_without_fix`.
- **Docker memory is 8 GB.** Installs run one at a time; vitest is limited to
  4 GB.
- **~45 patches is small,** and the by-outcome split is smaller still. The
  report gives counts beside shares, and the write-up says what the sample
  cannot separate.
