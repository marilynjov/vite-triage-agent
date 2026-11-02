# Phase 06a — Staged Analysis Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Split triage into locate → understand → hypothesize stages with typed outputs, attribute every failed run to a stage using the fix's own lines at the pin, and compare the staged pipeline with Phase 05's single agent on the same 40 val rows.

**Architecture:**
- `agent.loop.run` gains `output_model` and `check`.
- `agent/stages.py` holds the stage prompts, tool subsets and boundary checks. `agent/pipeline.py` runs the stages under one shared budget and returns a `PipelineRun` that duck-types `Run`.
- `eval/gold_regions.py` derives the fix's surviving lines with `git blame`. `eval/attribution.py` assigns each run one outcome.
- The ablation runner gains a `staged` arm. The report gains an outcome table and a "fails most" verdict.

**Tech Stack:** Python 3.12, `anthropic==1.9.0` (`claude-haiku-5-5`), Pydantic, git, pytest.

**Spec:** `docs/superpowers/specs/2026-11-02-phase-06a-staged-analysis-design.md`

## Global Constraints

- **Stage turn caps:** locate 8, understand 6, hypothesize 1. List caps: `LocateResult.files` 1–5, `UnderstandResult.regions` 1–6.
- **Tools per stage:**
  - locate: grep, list_dir, read_file, git_log
  - understand: read_file, grep
  - hypothesize: none (the request carries no `tools` key)
- **One pipeline `Budget`** (default `Budget.from_config()`). Tokens, seconds and dollars are summed across stages. Each stage gets `Budget(max_turns=<cap>, max_tokens=B.max_tokens - so_far, max_seconds=B.max_seconds - so_far, max_dollars=B.max_dollars - so_far)`, and a stage starting with any remaining limit ≤ 0 fails at once as a budget stop.
- **Outcomes**, exactly one per staged run, checked in this order: `error:<stage>`, `locate_miss`, `understand_miss`, `hypothesize_miss`, `hit`. `fault_hit` is a bool, or null when the row has no region gold. Ranges are inclusive and overlap when `a.start <= b.end and b.start <= a.end`. An empty `suspected_files` is never a hit.
- **Gold regions:**
  - fix commits = `data/raw/commits.jsonl` entries whose `pr_refs` ∩ the row's `linked_prs` is non-empty, or whose `closes_refs` contain the row's `issue`
  - lines = `git blame --line-porcelain <pin> -- <gold file>` lines blamed to a fix commit, grouped into contiguous ranges
  - cached in gitignored `data/gold_regions.jsonl`
- **Staged runs are eval-only.** The pipeline never passes `on_event` to the live registry, because `HypothesisResult` has a `fault` field that the `Done` event's `GroundedTriageResult` forbids. `/runs` is unchanged.
- **Records:** staged records go into the existing `data/runs/val-n40-r3.jsonl` with the normal fields plus `stages`, `stage_costs`, `failed_stage`, `outcome` and `fault_hit`. Records of other arms are unchanged.
- **Spend:** ≤ $1.50 new. Smoke test first (2 issues × 1 repeat). The full run's `--max-dollars` is the file's existing spend + 1.50.
- **Rules:** no paid calls in pytest; `MEASUREMENTS.md` is append-only; no attribution trailers; never commit the untracked `test-output.xml`. Work on branch `phase-06a-staged-analysis`, with one PR at the end.

## Review Focus

1. **An issue with several fix commits** (two linked PRs). Expected: gold regions are the union over all fix commits. Test in Task 4.
2. **Hypothesize returns an empty `suspected_files`.** Expected: never a `hit`. If understand overlapped, the outcome is `hypothesize_miss`. Test in Task 5.
3. **A region that touches a gold range at exactly one line,** or a single-line gold range. Expected: it counts as an overlap. Test in Task 5.
4. **A report over a file mixing staged and older-arm records,** where older records have no `outcome` key. Expected: the outcome table reads only staged records, and the other arms' tables are unchanged. Test in Task 6.
5. **A stage that reads files outside the candidates** (locate's tools) and returns one that exists in scope. Expected: allowed. Only nonexistent or out-of-scope paths are rejected. Test in Task 2.

> Amended 2026-11-02 (ruling 5, commit ec1d544): locate and understand prune out-of-scope items and fail only when none remain; see ARCHITECTURE.md §9.

---

### Task 1: The loop takes an output model and a check

**Files:**
- Modify: `agent/loop.py`
- Test: `tests/test_loop.py`

**Interfaces:**
- Produces: `run(..., output_model: type[BaseModel] = GroundedTriageResult, check: Callable[[BaseModel], None] | None = None, ...) -> Run`.
  - `None` means `check_in_scope`.
  - The request's `output_config` schema is `anthropic.transform_schema(output_model)`, computed once per model (cache by class).
  - The final text is validated with `output_model.model_validate_json`, then `check(result)`. A `ValueError`/`ValidationError` from either becomes a `RunError`, as today.
  - The `done` event still carries `result.model_dump()`.

- [ ] **Step 1: Failing tests:**

```python
def test_output_model_is_sent_and_validated()     # run(..., output_model=Tiny) with Tiny(BaseModel){answer:str}: kwargs output_config schema == transform_schema(Tiny); run.result is a Tiny
def test_custom_check_rejection_is_a_run_error()  # check raises ValueError("nope") → RunError, message contains "nope"
def test_defaults_unchanged()                     # no output_model/check → GroundedTriageResult schema, check_in_scope applied (out-of-scope path → RunError)
```

- [ ] **Step 2: Verify they fail; implement; the full suite passes** (181 + new).

- [ ] **Step 3: Commit**: `Loop: output model and result check as parameters`.

---

### Task 2: Stage contracts and boundary checks

**Files:**
- Modify: `api/schema.py`, `agent/tools.py`
- Create: `agent/stages.py` (checks only in this task)
- Test: `tests/test_stage_contracts.py`

**Interfaces:**
- Consumes: `check_in_scope`, `tools._safe`/`_kind`, `M.is_gold_candidate`.
- Produces:
  - **`api/schema.py`:** `LocatedFile`, `LocateResult`, `Region`, `UnderstandResult`, `Fault` and `HypothesisResult(GroundedTriageResult)`, all with `extra="forbid"` and fields exactly as the spec §1. `files` and `regions` carry list caps `min_length=1`, `max_length=5` and `6`.
  - **`agent/tools.py`:** `file_length(path: str) -> int | None`, the number of lines at the pin using `_split_lines`, or `None` for a missing file or a directory.
  - **`agent/stages.py`:**
    - `check_locate() -> Callable[[LocateResult], None]`
    - `check_understand(located: LocateResult) -> Callable[[UnderstandResult], None]`
    - `check_hypothesis(located: LocateResult, understood: UnderstandResult) -> Callable[[HypothesisResult], None]`
    - Each raises `ValueError` whose message starts with the stage name (`"locate: "`, `"understand: "`, `"hypothesize: "`).

- [ ] **Step 1: Failing tests** (the real repo at the pin; `CSS = "packages/vite/src/node/plugins/css.ts"`):

```python
def test_models_validate_and_forbid_extra()
def test_locate_accepts_in_scope_files_outside_candidates()    # Review Focus 5
def test_locate_rejects_missing_or_out_of_scope_path()        # "locate: " prefix
def test_understand_rejects_region_outside_located_files()
def test_understand_rejects_out_of_bounds_lines()             # end_line > file_length(CSS); start_line 0; start > end
def test_hypothesis_rejects_fault_outside_understood_files()
def test_hypothesis_rejects_out_of_scope_suspected_file()
def test_file_length()                                        # file_length(CSS) == 3685; directory → None; missing → None
```

- [ ] **Step 2: Verify they fail; implement; regenerate nothing.** These models are not UI types. The full suite passes.

- [ ] **Step 3: Commit**: `Stage contracts: typed results and boundary checks for locate, understand, hypothesize`.

---

### Task 3: Stage prompts and the pipeline

**Files:**
- Modify: `agent/stages.py`
- Create: `agent/pipeline.py`
- Test: `tests/test_pipeline.py`

**Interfaces:**
- Consumes: `run(output_model=, check=, system=, tool_specs=, budget=, client=)` and `Budget` (Task 1 and Phase 05); Task 2 models and checks; `api.triage.grounded_prompt`, `STRUCTURED_SYSTEM`; `tools.TOOL_SPECS`.
- Produces:
  - **`agent/stages.py`:**
    - `LOCATE_SYSTEM`, `UNDERSTAND_SYSTEM`, `HYPOTHESIZE_SYSTEM`. Hypothesize builds on `STRUCTURED_SYSTEM`, because it fills the triage fields. Each states its single job per the spec §1 "Prompts" and keeps the evidence rules.
    - `LOCATE_TOOLS = TOOL_SPECS`, `UNDERSTAND_TOOLS` (the read_file and grep specs only), `HYPOTHESIZE_TOOLS = []`.
    - `understand_prompt(title, body, located: LocateResult) -> str` and `hypothesize_prompt(title, body, located, understood) -> str`. The hypothesize prompt includes both results as JSON.
  - **`agent/pipeline.py`:**
    - `STAGE_TURNS = {"locate": 8, "understand": 6, "hypothesize": 1}`.
    - `@dataclass PipelineRun`:
      - `locate`, `understand`, `hypothesis` (results or `None`)
      - `runs: dict[str, Run]`, `failed_stage: str | None`, `error: str | None`
      - properties `result` (= `hypothesis`), `turns` (concatenated `Turn`s), `trace` (concatenated `ToolCall`s), `input_tokens`, `output_tokens`, `cost_usd`, `stage_costs: dict[str, float]`
      - so it duck-types `Run` for the ablation runner
    - `class PipelineError(RunError)` with `.run: PipelineRun` and `.stage: str`.
    - `run_pipeline(title, body, candidates, cutoff, *, budget=None, client=None) -> PipelineRun`. It raises `PipelineError` on any stage failure, including a budget stop or a stage that starts with nothing left.

- [ ] **Step 1: Failing tests** (FakeClient scripting three stages):

```python
def test_stages_run_in_order_with_their_tools()       # recorded requests: locate tools == TOOL_SPECS; understand tool names == {"read_file","grep"}; hypothesize request has no "tools" key
def test_each_stage_sees_the_previous_result()        # understand request's first user message contains a located path; hypothesize contains both JSON results
def test_result_is_the_hypothesis()                   # PipelineRun.result.fault.path == …; stage_costs has three keys
def test_budget_carries_across_stages()               # tiny max_dollars: locate spends it → understand fails at once; PipelineError.stage == "understand"
def test_understand_run_error_is_attributed()         # understand returns a region outside located files → PipelineError.stage == "understand"; .run.locate set, .run.understand None
def test_pipeline_run_duck_types_run()                # len(turns) == sum of stage turns; cost_usd == sum
```

- [ ] **Step 2: Verify they fail; implement; the full suite passes.**

- [ ] **Step 3: Commit**: `Staged pipeline: locate, understand, hypothesize under one budget`.

---

### Task 4: Gold regions

**Files:**
- Create: `eval/gold_regions.py`
- Test: `tests/test_gold_regions.py`

**Interfaces:**
- Consumes: `mining.config` (`RAW`, `PINNED_SHA`, `REPO_PATH`, `DATA`), `data/raw/commits.jsonl`.
- Produces:
  - `fix_commits(row: dict, commits: list[dict]) -> list[str]`
  - `regions_for(row: dict, commits: list[dict]) -> dict[str, list[list[int]]]`: gold file → `[[start, end], ...]` inclusive pin line ranges; files with no surviving lines map to `[]`
  - `load_regions(rows: list[dict]) -> dict[int, dict[str, list[list[int]]]]`: by issue, built and cached in `data/gold_regions.jsonl`, reusing the cache for issues already present

- [ ] **Step 1: Failing tests** (the real clone; issue #13907 is a val row whose fix is PR #13909, gold `packages/vite/src/node/plugins/esbuild.ts`):

```python
def test_fix_commits_for_13907_include_the_13909_commit()
def test_regions_for_13907_read_back_from_the_pinned_file()   # ≥1 range; every range within file_length; tools.read_file of the range is non-empty
def test_union_over_several_fix_commits()                     # synthetic row with two linked PRs whose commits touched the same file → ranges from both (Review Focus 1); build the row from two real commits.jsonl entries
def test_deletion_only_fix_has_no_regions()                   # monkeypatch blame output with no lines from the fix sha → []
def test_cache_is_reused(tmp_path, monkeypatch)               # second load does not call git blame
```

- [ ] **Step 2: Verify they fail; implement; the full suite passes.** Group contiguous blame lines into ranges. Use `subprocess` with the same literal-pathspec style as `agent/tools.py`.

- [ ] **Step 3: Commit**: `Gold regions: the fix's own lines at the pin, via git blame`.

---

### Task 5: Attribution

**Files:**
- Create: `eval/attribution.py`
- Test: `tests/test_attribution.py`

**Interfaces:**
- Produces:
  - `overlaps(a: list[int], b: list[int]) -> bool`
  - `outcome(stages: dict, failed_stage: str | None, gold_files: list[str], regions: dict[str, list[list[int]]]) -> tuple[str, bool | None]`
  - `stages` is `{"locate": dict | None, "understand": dict | None, "hypothesis": dict | None}`, as dumped stage results. The function returns `(outcome, fault_hit)` per Global Constraints.

- [ ] **Step 1: Failing tests:**

```python
def test_error_outcome_names_the_stage()
def test_locate_miss()
def test_understand_miss_with_region_gold()
def test_understand_miss_file_level_when_no_region_gold()     # regions all [] → needs a region in a gold file
def test_hypothesize_miss()
def test_empty_suspected_files_is_not_a_hit()                 # Review Focus 2
def test_hit_with_fault_hit_true_and_false()
def test_fault_hit_is_none_without_region_gold()
def test_touching_ranges_overlap()                            # [10,20] & [20,30]; single-line [5,5] & [5,5] (Review Focus 3)
```

- [ ] **Step 2: Verify they fail; implement; the full suite passes. Commit**: `Attribution: one outcome per staged run`.

---

### Task 6: The staged arm and the outcome report

**Files:**
- Modify: `eval/arms.py`, `eval/ablation.py`, `eval/ablation_report.py`
- Test: `tests/test_arms.py`, `tests/test_ablation.py`, `tests/test_ablation_report.py`

**Interfaces:**
- Consumes: `run_pipeline`, `PipelineError` (Task 3); `load_regions` (Task 4); `outcome` (Task 5).
- Produces:
  - **`eval/arms.py`:** `Arm` gains `kind: str = "loop"`; `ARMS["staged"] = Arm("staged", None, [], kind="pipeline")`. `MODEL_ARMS` stays unchanged, so the decision rule and the chain are untouched; add `PIPELINE_ARMS = ("staged",)`.
  - **`eval/ablation.py`:**
    - `make_record(..., run_fn=run, pipeline_fn=run_pipeline)` dispatches on `arm.kind`.
    - A staged record adds `stages`, `stage_costs`, `failed_stage`, `outcome` and `fault_hit`. Gold regions are loaded once per CLI run.
    - `--arms staged` is accepted.
  - **`eval/ablation_report.py`:**
    - `outcome_table(records, arm="staged", subset="clean") -> dict[str, tuple[float, float, float]]`: share per outcome as (mean over repeats, min, max).
    - `fails_most(records, arm="staged") -> str`: the miss outcome or `error:<stage>` with the largest clean mean share.
    - `markdown()` prints both. It also prints the count of rows with file-level-only attribution and the `fault_hit` rate among hits.
    - The staged arm appears in the paired "against grounded" table, and in a new "staged against agent" line.

- [ ] **Step 1: Failing tests:**

```python
# test_arms.py
def test_staged_arm_is_a_pipeline_arm_and_not_in_model_arms()
# test_ablation.py
def test_staged_record_has_stage_fields_and_outcome()     # fake pipeline_fn; keys == FIELDS | {"stages","stage_costs","failed_stage","outcome","fault_hit"}
def test_staged_pipeline_error_records_failed_stage()     # PipelineError(stage="locate") → outcome "error:locate", scores 0, cost kept
def test_other_arms_records_unchanged()                   # agent arm keys == FIELDS
# test_ablation_report.py
def test_outcome_table_shares_and_spread()
def test_fails_most_picks_the_largest_miss()
def test_report_survives_mixed_records()                  # staged + older-arm records without outcome keys (Review Focus 4)
def test_staged_against_agent_line()
```

- [ ] **Step 2: Verify they fail; implement; the full suite passes. Commit**: `Staged arm in the ablation runner; outcome table and fails-most in the report`.

---

### Task 7: Smoke test, the 40-row run, and the measurements (paid)

**Files:**
- Modify: `MEASUREMENTS.md` (append)

- [ ] **Step 1: Smoke test (≤ $0.05).** `PROVIDER=anthropic python -m eval.ablation --arms staged --n 2 --repeats 1 --name smoke-06a`. Expect 2 records with outcomes, and no 401 or api errors. Report the cost per issue. Delete `data/runs/smoke-06a.jsonl`.

- [ ] **Step 2: Full run.**
  1. Compute `existing = sum(cost_usd)` over `data/runs/val-n40-r3.jsonl`.
  2. Run `PROVIDER=anthropic python -m eval.ablation --arms staged --n 40 --repeats 3 --max-dollars <existing + 1.50>` in the background, logging to the workspace.
  3. Expect 120 new records. If the smoke test showed more than $0.012 per issue, stop and report before running.

- [ ] **Step 3: Report.** `python -m eval.ablation --report data/runs/val-n40-r3.jsonl`, saved verbatim.

- [ ] **Step 4: Append "Phase 06a — staged analysis (40 rows × 3 repeats)" to `MEASUREMENTS.md`.**
  - staged vs agent, paired (recall@1/@5, MRR, intervals), plus cost per triage and per correct answer and p50/p95 latency
  - the outcome table (clean and all, with spreads)
  - file-level-only rows and the `fault_hit` rate
  - **the gate sentence:** which stage fails most, and what to fix first, with the mechanism
  - caveats: 33 clean rows; post-fix code at the pin; gold regions only for surviving lines
  - numbers exactly as printed; interpretations labelled

- [ ] **Step 5: Commit**: `Measure Phase 06a: <one-line finding>`.

---

### Task 8: Docs

**Files:**
- Modify: `ARCHITECTURE.md`, `README.md`

- [ ] **Step 1: `ARCHITECTURE.md`.** Add a "Staged analysis" section covering the three stages and their contracts, the pipeline budget, gold regions via blame, the outcome order, and "eval-only, not on `/runs`". Update the layout table and "Where the next phases attach" (Pieces B and C).

- [ ] **Step 2: `README.md`.** Add the Phase 06 status row with the headline and the gate sentence, and the staged ablation command in the Measuring table.

- [ ] **Step 3: Commit**: `ARCHITECTURE, README: staged analysis`.
