# Phase 05a — Does Looking Pay? Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Separate the agent prompt's effect from the tools' effect with four ablation arms run through one loop, measure run-to-run variation with repeats, and confirm the winning arm on all 206 val rows.

**Architecture:** `agent.loop.run` gains `system` and `tool_specs` parameters, so every arm uses the same code path. `eval/arms.py` names the arms. `eval/ablation.py` runs (arm, issue, repeat) jobs on a 4-worker pool, appending one JSON record per run, with resume and a spend cap. `eval/ablation_report.py` reads only those records and computes scores, paired bootstrap intervals, flip rate, tool and cost stats, and the pre-registered decision rule.

**Tech Stack:** Python 3.12, `anthropic==1.9.0` (`claude-haiku-5-5`), pytest, stdlib only for the statistics (`random`, `statistics`, `concurrent.futures`, `threading`).

**Spec:** `docs/superpowers/specs/2026-11-01-phase-05a-does-looking-pay-design.md`

## Global Constraints

- Arms, verbatim names: `grounded`, `agent-no-tools`, `agent`, `agent-look`, `retrieval`.
- `LOOK_FIRST` text, verbatim: "If your conclusion depends on code or documents you cannot see in the excerpts, use a tool to check it before you conclude. Name a file outside the candidates only after a tool showed that it exists and fits the report."
- All model arms run through `agent.loop.run`. With `tool_specs=[]` the request omits the `tools` key.
- Retrieval: strategy `fixed_v1_header`, k = `C.GROUNDED_K` (10), once per issue, shared by all arms and repeats.
- Rows: the fixed val slice (sort by `issue`, take `--n`). Cutoff per run = `row["created_at"]`.
- Run records: `data/runs/<name>.jsonl` (gitignored), fields exactly as in spec §3, plus `split` and `error_kind` (see Review Focus 1).
- Workers: 4. `--max-dollars` default 3. Prior cost per run: `grounded` 0.0018, `agent-no-tools` 0.0018, `agent` 0.0029, `agent-look` 0.0045, `retrieval` 0.
- Bootstrap: 10,000 resamples of issues, fixed seed 0, 95% interval. A difference counts only if the interval excludes zero.
- Clean subset = rows where `mentions_gold_path` is false. Clean is the quoted number.
- Decision rule: the agent arm (`agent-no-tools`, `agent`, `agent-look`) with the highest clean recall@1 whose paired clean recall@1 interval against `grounded` excludes zero (lower bound > 0); otherwise `grounded`.
- No paid API call inside `pytest`. `MEASUREMENTS.md` is append-only. Commits carry no attribution trailer. The test split is never read.
- All work happens on branch `phase-05a-does-looking-pay`, merged with a PR at the end (per-phase-piece PR, merge commit).

## Review Focus

1. **An infrastructure failure** (a 429/connection error after SDK retries, or any non-`RunError` exception) should not be scored as a model failure. Expected: the record gets `error_kind: "api"`, is excluded from scores and counted separately, and is retried on resume. A `RunError` from the model's own output gets `error_kind: "run"` and scores zero. Tests in Task 2 and Task 4.
2. **An interrupted run** (Ctrl-C or a crash mid-write) leaves a partial last line. Expected: loading skips an unparseable trailing line, and resume re-runs that job. Test in Task 2.
3. **Re-running into the same file with a different `--split`.** Expected: the runner refuses rather than mixing slices. Test in Task 2.
4. **A report over a file missing an arm**, or two arms with no shared issues. Expected: those comparisons print "no data", never a crash. Test in Task 4.
5. **An arm with zero rank-1 hits** (e.g. it always abstains). Expected: cost per correct answer prints "n/a", not a ZeroDivisionError. Test in Task 4.

---

### Task 1: Arms through one loop

**Files:**
- Modify: `agent/loop.py` (`run` signature and the request call)
- Create: `eval/arms.py`
- Test: `tests/test_arms.py`

**Interfaces:**
- Consumes: `agent.loop.run`, `AGENT_SYSTEM`, `agent.tools.TOOL_SPECS`, `api.triage.GROUNDED_SYSTEM`.
- Produces:
  - `run(title, body, candidates, cutoff, *, system: str = AGENT_SYSTEM, tool_specs: list[dict] | None = None, max_turns=None, client=None) -> Run`. `None` means `tools.TOOL_SPECS`; `[]` means no tools.
  - `eval/arms.py`: `LOOK_FIRST: str`; `@dataclass(frozen=True) class Arm: name: str; system: str | None; tool_specs: list[dict]`; `ARMS: dict[str, Arm]` with the five arms (`retrieval` has `system=None`); `MODEL_ARMS = ("grounded", "agent-no-tools", "agent", "agent-look")`.

- [ ] **Step 1: Write the failing tests** in `tests/test_arms.py`, reusing the FakeClient pattern from `tests/test_loop.py`:

```python
def test_arms_table_has_the_five_arms_with_the_right_prompt_and_tools():
    assert set(ARMS) == {"grounded", "agent-no-tools", "agent", "agent-look", "retrieval"}
    assert ARMS["grounded"].system == GROUNDED_SYSTEM and ARMS["grounded"].tool_specs == []
    assert ARMS["agent-no-tools"].system == AGENT_SYSTEM and ARMS["agent-no-tools"].tool_specs == []
    assert ARMS["agent"].tool_specs == TOOL_SPECS
    assert ARMS["agent-look"].system == AGENT_SYSTEM + "\n\n" + LOOK_FIRST
    assert ARMS["retrieval"].system is None

def test_look_first_is_the_spec_text(): assert LOOK_FIRST.startswith("If your conclusion depends on code or documents")
def test_no_tools_request_omits_the_tools_key()      # run(..., tool_specs=[]) → recorded kwargs lack "tools"; output_config present
def test_no_tools_run_ends_after_one_turn()           # one end_turn response → 1 turn, result validated
def test_custom_system_is_sent()                      # run(..., system="X") → kwargs["system"] == "X"
def test_defaults_are_unchanged()                     # run(...) → system == AGENT_SYSTEM, tools == TOOL_SPECS
```

- [ ] **Step 2: Run them and verify they fail** (`ImportError: eval.arms`). Run: `python -m pytest tests/test_arms.py -q`

- [ ] **Step 3: Implement.** In `run`, resolve `tool_specs = tools.TOOL_SPECS if tool_specs is None else tool_specs` and build the request kwargs as a dict, adding `tools` only when non-empty. Write `eval/arms.py` per the interface, with `LOOK_FIRST` verbatim from Global Constraints and a module docstring explaining that adjacent arms differ in one thing.

- [ ] **Step 4: Run the full suite.** `python -m pytest -q` → all pass (66 earlier + new).

- [ ] **Step 5: Commit**: `Ablation arms: one loop, prompt and tools as parameters`.

---

### Task 2: The runner and its records

**Files:**
- Create: `eval/ablation.py`
- Test: `tests/test_ablation.py`

**Interfaces:**
- Consumes: `ARMS`, `MODEL_ARMS` (Task 1); `agent.loop.run`, `RunError`; `eval.retrieve.query_for`, `score_ranking`; `retrieval.search.search_ranked`; `mining.config.SPLIT_DIR`.
- Produces:
  - `load_records(path: Path) -> list[dict]`: skips blank and unparseable lines.
  - `done_keys(records) -> set[tuple[str, int, int]]`: `(arm, issue, repeat)` for records whose `error_kind` is not `"api"`.
  - `make_record(arm, row, repeat, candidates: list[str], split: str, *, run_fn=run) -> dict`: runs one job, never raises. Fields per spec §3 plus `split` and `error_kind` (`None` on success, `"run"` for `RunError`, `"api"` for a `RunError` whose message starts with `"API error"` and for any other exception). `retrieval` scores the candidate order with zero cost and no call. `tool_calls` entries carry `output_preview` from `ToolCall.output`.
  - `run_jobs(jobs, path, *, workers=4, cap_dollars=None, run_fn=run) -> dict` with `{"ran": int, "spent": float, "stopped_for_cap": bool}`. It appends each record as one line and flushes, under a lock.
  - CLI `python -m eval.ablation --arms A,B --split val --n 40 --repeats 3 [--name] [--max-dollars 3]`. The default name is `<split>-n<n>-r<repeats>`. It refuses if the file's records carry a different `split`.

- [ ] **Step 1: Write the failing tests** with a fake `run_fn` (no API, no DB). Build rows as dicts with `issue`, `title`, `body`, `created_at`, `gold_files`, `mentions_gold_path`.

```python
def test_record_has_every_spec_field()            # keys == spec §3 fields | {"split", "error_kind"}
def test_success_scores_with_score_ranking()      # picked ["a","b"], gold {"b"} → recall_at_1 0, recall_at_5 1, rr 0.5
def test_run_error_scores_zero_and_keeps_paid_turns()   # fake raises RunError("bad", run_with_2_turns) → error_kind "run", turns 2, cost > 0, recall 0
def test_api_error_is_marked_api()                # RunError("API error on turn 2: 529", ...) and a plain RuntimeError → error_kind "api"
def test_retrieval_arm_needs_no_model()           # arm "retrieval": run_fn never called, cost 0, scored from candidates
def test_cutoff_is_the_rows_created_at()          # fake records its cutoff arg == row["created_at"]
def test_load_skips_a_partial_last_line(tmp_path) # file with 2 good lines + '{"arm": "ag' → 2 records
def test_resume_skips_done_but_retries_api_errors(tmp_path)  # done_keys excludes error_kind "api"
def test_jobs_are_appended_one_line_each(tmp_path)  # run_jobs over 6 fake jobs → 6 lines, each valid JSON
def test_refuses_a_file_from_another_split(tmp_path)  # existing records split "train", CLI split "val" → SystemExit
```

- [ ] **Step 2: Run them and verify they fail.** Run: `python -m pytest tests/test_ablation.py -q`

- [ ] **Step 3: Implement `eval/ablation.py`** to the interfaces. Build jobs as (arm, row, repeat) for every arm × row × repeat whose key is not in `done_keys`, with candidates computed once per row (paths kept, FileHits passed to `run_fn`). For model arms, call `run_fn(title, body, candidates, row["created_at"], system=arm.system, tool_specs=arm.tool_specs)`. Use `concurrent.futures.ThreadPoolExecutor(workers)`. Print a one-line progress counter, as `eval/agent.py` does.

- [ ] **Step 4: Run the full suite.** All pass.

- [ ] **Step 5: Commit**: `Ablation runner: one record per run, resume, infrastructure errors retried`.

---

### Task 3: Spend control

**Files:**
- Modify: `eval/ablation.py`
- Test: `tests/test_ablation.py`

**Interfaces:**
- Consumes: Task 2's `run_jobs`, `load_records`.
- Produces:
  - `PRIOR_COST: dict[str, float]` with the values from Global Constraints.
  - `estimate_cost(jobs, records) -> float`: per arm, the mean `cost_usd` of that arm's successful existing records if any, else `PRIOR_COST[arm]`, times its pending jobs.
  - The CLI prints the estimate and refuses with `SystemExit` when it exceeds `--max-dollars`.
  - `run_jobs(..., cap_dollars=X)`: once the summed actual cost passes X, it starts no new job, lets in-flight jobs finish, and returns `stopped_for_cap=True`.

- [ ] **Step 1: Failing tests.**

```python
def test_estimate_uses_prior_cost_without_records()   # 10 "agent" jobs, no records → 10 * 0.0029
def test_estimate_prefers_measured_cost()             # 2 existing agent records at 0.004 → pending * 0.004
def test_cli_refuses_when_estimate_exceeds_cap(tmp_path, monkeypatch)  # tiny --max-dollars → SystemExit, file untouched
def test_run_stops_once_spend_passes_cap(tmp_path)   # fake run costs 0.01 each, cap 0.025, workers=1, 10 jobs → ran == 3, stopped_for_cap
```

- [ ] **Step 2: Verify they fail**, then **implement**: a lock-guarded running total checked before each job starts.

- [ ] **Step 3: Full suite passes. Commit**: `Ablation spend cap: refuse to start over budget, stop cleanly when passed`.

---

### Task 4: The report

**Files:**
- Create: `eval/ablation_report.py`
- Modify: `eval/ablation.py` (`--report PATH` calls `ablation_report.main`)
- Test: `tests/test_ablation_report.py`

**Interfaces:**
- Consumes: run records (Task 2 format), `MODEL_ARMS`.
- Produces (records exclude `error_kind == "api"` everywhere except the error counts):
  - `arm_scores(records, arm, subset: str) -> dict[str, tuple[float, float, float]]`: metric → (mean of per-repeat means, min, max) for `recall_at_1`, `recall_at_5`, `rr`. `subset` is `"all"` or `"clean"`.
  - `paired(records, a, b, *, metric="recall_at_1", subset="clean", n_boot=10_000, seed=0) -> dict | None`: per issue, mean over repeats; over shared issues returns `wins`, `losses`, `ties` (b vs a), `diff` (mean of b − a), `ci_low`, `ci_high`. Returns `None` when there are no shared issues.
  - `flip_rate(records, arm, subset) -> float | None`: the share of issues with ≥2 repeats whose rank-1 hit (`recall_at_1 > 0`) differs across repeats.
  - `tool_stats(records, arm) -> dict`: `used_any` share, turn distribution, calls per tool name, tool error rate, `picks_seen` (the share of picked files appearing in any `output_preview`).
  - `cost_stats(records, arm) -> dict`: `per_triage`, `per_correct` (`None` when there are no rank-1 hits), `p50_ms`, `p95_ms`, `run_errors`, `api_errors`.
  - `choose_arm(records) -> str`: the decision rule from Global Constraints.
  - `markdown(records) -> str` and `main(path)`, which prints the tables and the chosen arm.

- [ ] **Step 1: Failing tests** on hand-made records (a small helper builds them):

```python
def test_arm_scores_mean_and_spread()          # repeats with clean recall@1 means 0.3, 0.4, 0.5 → (0.4, 0.3, 0.5)
def test_clean_subset_excludes_named_gold()    # a mentions_gold_path row changes "all" but not "clean"
def test_api_errors_are_excluded_from_scores() # an api-error record does not lower the mean; it is counted in cost_stats["api_errors"]
def test_paired_counts_wins_losses_ties()
def test_bootstrap_interval_excludes_zero_for_a_clear_gap()   # b better on every issue → ci_low > 0
def test_bootstrap_interval_includes_zero_for_no_gap()        # identical arms → ci_low <= 0 <= ci_high
def test_bootstrap_is_deterministic()          # two calls, same result
def test_paired_returns_none_without_shared_issues()
def test_flip_rate()                           # 2 of 4 issues flip → 0.5
def test_cost_per_correct_is_none_without_hits()
def test_picks_seen()                          # a picked path present in a tool output_preview counts
def test_choose_arm_picks_best_qualifying_agent_arm()
def test_choose_arm_falls_back_to_grounded()   # no agent arm clears grounded → "grounded"
def test_markdown_survives_a_missing_arm()     # records without "agent-look" → no exception, says "no data"
```

- [ ] **Step 2: Verify they fail**, then **implement**. Bootstrap: resample the shared issue list with replacement using `random.Random(seed)`; the interval is the 2.5th and 97.5th percentiles of the resampled mean differences.

- [ ] **Step 3: Full suite passes. Commit**: `Ablation report: paired bootstrap intervals, flip rate, cost per correct answer, decision rule`.

---

### Task 5: The 40-row ablation (paid, ~$1.40)

**Files:**
- Modify: `MEASUREMENTS.md` (append)

- [ ] **Step 1: Smoke test**: `PROVIDER=anthropic python -m eval.ablation --arms grounded,agent-no-tools,agent,agent-look --n 2 --repeats 1 --name smoke`. Expect 8 records, no `api` errors, under $0.05. Delete `data/runs/smoke.jsonl` afterwards.

- [ ] **Step 2: Run**: `PROVIDER=anthropic python -m eval.ablation --arms grounded,agent-no-tools,agent,agent-look --n 40 --repeats 3`. Expect 480 records, under $3. If it stops for the cap or for errors, re-run the same command: resume continues.

- [ ] **Step 3: Report**: `python -m eval.ablation --report data/runs/val-n40-r3.jsonl`. Save the output.

- [ ] **Step 4: Append "Phase 05a — does looking pay? (40 rows × 3 repeats)" to `MEASUREMENTS.md`.** Include:
  - the per-arm table with spreads
  - each comparison along the chain with wins/losses/ties and its interval
  - the flip rate
  - tool behaviour
  - cost per triage and per correct answer
  - the arm the decision rule chose
  - one paragraph per finding with its mechanism
  - caveats: arm `grounded` vs Phase 04's grounded, post-fix code, 33 clean rows

  State plainly any difference whose interval includes zero.

- [ ] **Step 5: Commit**: `Measure the 40-row ablation: <one-line finding>`.

---

### Task 6: The 206-row confirmation (paid, ~$1) and docs

**Files:**
- Modify: `MEASUREMENTS.md` (append), `ARCHITECTURE.md` (eval section), `README.md` (the Phase 05 row of the status table)

- [ ] **Step 1: Run**: `PROVIDER=anthropic python -m eval.ablation --arms retrieval,grounded,<winner from Task 5> --n 206 --repeats 1`. When the winner is `grounded`, the arms are `retrieval,grounded`.

- [ ] **Step 2: Report** on `data/runs/val-n206-r1.jsonl`.

- [ ] **Step 3: Append "Phase 05a — confirmation on all 206 val rows" to `MEASUREMENTS.md`.** Include:
  - the three-way table
  - the paired interval of the winner against grounded over 175 clean rows
  - cost per correct answer for each arm
  - the gate verdict in one sentence: did agency pay for itself, at what cost

- [ ] **Step 4: `ARCHITECTURE.md`.** Add the arms table and the run-record format to the eval section, plus one sentence on `run(system=, tool_specs=)`.

- [ ] **Step 5: `README.md`.** Fill the Phase 05 row's headline with the confirmed result.

- [ ] **Step 6: Commit** the docs as two commits: measurements, then architecture + README.
