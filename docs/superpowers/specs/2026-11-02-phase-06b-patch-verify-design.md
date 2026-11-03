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
| Rows | Issues whose fix changed a file under `__tests__/`: 23 of the 40-row slice; 22 of them have a stored 06a hypothesis | The other 16 have no tests to fail, and one has only playground tests (needs a browser). |
| Patch input | Reuse the stored 06a hypotheses (`val-n40-r3.jsonl`, arm `staged`) | One model call per run (≈ $0.01) instead of re-running the pipeline (≈ $0.03) plus a retrieval index per pre-fix checkout. |
| Stale hypotheses | Recorded as `stale_path`, no model call | 06a read the 2026 pin; 9 of the 41 reusable hypotheses name a file absent before the 2023 fix. The count measures how much the pin warped 06a. |
| Feedback | One attempt, graded blind | The gold tests are the answer key; showing their output to the model would leak it. |
| Where tests run | Docker | 2023 checkouts pin `pnpm@8.6.7` and Node ≤ 20; the host has Node 23. The test run executes model-written code, so it runs with no network. Matches roadmap p6d. |
| Budget | ≤ $0.60 lifetime on the patch run file | ≈ 32 calls at ≈ $0.01, with headroom; the smoke test measures the real figure. |

## 1. Harness validation (no model calls)

`eval/verify_harness.py`, run once per candidate issue; results cached in
gitignored `data/verify_harness.jsonl`.

1. **Fix commit:** the row's commit in `data/raw/commits.jsonl` (as
   `eval/gold_regions.py` finds it). **Pre-fix commit:** its first parent.
   **Gold tests:** the fix's changed files whose path contains `/__tests__/`,
   taken whole at the fix commit. **Fix source:** the fix's other changed files
   under `packages/vite/src`.
2. **Prepare** (network on): in the container, check out the pre-fix commit
   into a per-issue directory under a Docker volume, enable corepack, and run
   `pnpm install --frozen-lockfile` against a shared pnpm store volume. This
   checkout is the cached base for every later verify of the issue.
3. **Fail check** (network off): copy of the base + gold tests →
   `pnpm exec vitest run <gold tests>` must exit nonzero.
4. **Pass check** (network off): copy of the base + gold tests + fix source at
   the fix commit → the same command must exit zero.

Anything else is `harness_invalid` with a reason (`install_failed`,
`passes_without_fix`, `fails_with_fix`, `timeout`). Those issues are excluded
from the run and listed in the report. The pass check also validates verify
itself: if the real fix does not pass, the harness is wrong, not the model.

**Container:** one image from `node:18` with `git` and the repo mounted
read-only. Every test run uses `--network none`, `--cpus 4`, `--memory 4g` and a
300 s timeout. A copy of the base is made with `cp -a --reflink=auto` inside the
volume, so the install is never repeated.

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

**Verify** (`eval/verify.py`): `verify(issue, edits) -> VerifyResult` makes a
copy of the issue's base, writes the edited files, copies in the gold tests and
runs the gold tests under the container limits above. It returns `passed`,
`exit_code`, `seconds` and the output tail (last 4 KB).

**Outcome per run**, exactly one:

| Outcome | Condition |
|---|---|
| `harness_invalid` | the issue failed §1; not counted in any rate |
| `stale_path` | the hypothesis's `fault.path` does not exist at the pre-fix commit; no model call |
| `patch_error` | the stage raised (`RunError`, budget stop, failed check) |
| `test_fail` | the edits applied but the gold tests failed or timed out |
| `pass` | the gold tests passed |

**The run** (`eval/patch_eval.py`): reads the arm-`staged` records in
`data/runs/val-n40-r3.jsonl` that have a hypothesis and whose issue passed §1,
and writes one record per source record to `data/runs/val-n40-patch.jsonl`.
Each record carries: `issue`, `repeat`, the source record's `outcome` and
`fault_hit`, `prefix_sha`, `patch` (the `PatchResult` or null), `diff`,
`verify`, `outcome`, `cost_usd`, `turns` and `error`. It resumes on
`(issue, repeat)` and refuses to start past `--max-dollars` (lifetime per file,
default 0.60), like `eval/ablation.py`.

1. Smoke test: `--issues <2 ids>`. Measure cost per patch.
2. Full run over every eligible record.

**The report** (`eval/patch_report.py`):
- the outcome table: count and share of each outcome, with min–max across the
  two repeats
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
  issue #13907's real fix (`c89f677`) passes verify, and the empty patch fails.

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
- Playground tests, and the issues whose fixes have no tests.
- Running the full existing suite as a regression check.
- The UI (Piece C).

## Risks

- **2023 installs may not reproduce.** A lockfile can name packages that are
  gone or need native builds. §1 runs before any spend and reports how many
  issues survive. If fewer than ~12 survive, stop and decide before paying.
- **Some gold tests may not fail without the fix** (the fix changed a test for
  other reasons). §1 excludes them as `passes_without_fix`.
- **Docker memory is 8 GB.** Installs run one at a time; vitest is limited to
  4 GB.
- **~32 patches is small,** and the by-outcome split is smaller still. The
  report gives counts beside shares, and the write-up says what the sample
  cannot separate.
