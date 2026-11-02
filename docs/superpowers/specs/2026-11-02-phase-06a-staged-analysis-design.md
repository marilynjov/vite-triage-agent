# Phase 06, Piece A — Staged analysis: design

Date: 2026-11-02 · Status: approved in conversation, awaiting spec review

## Purpose

Phase 06's gate: *name which stage fails most and what you'd fix first*. One
undifferentiated agent loop cannot answer that — a miss has no address. Piece A
splits triage into three named stages — **locate → understand → hypothesize** —
each a separate model call with its own prompt, tools and typed output, and
attributes every failed run to the stage where it went wrong. It is scored
against the Phase 05 single agent on the same 40 val rows.

Phase 06 is split into pieces:
- **A, this spec:** staged analysis and attribution.
- **B:** patch and verify. This needs a per-issue checkout from before the fix,
  because at the pin every val fix is already applied and any patch would
  "pass". It is decided after A.
- **C:** the stage-segmented trace and a diff viewer in the UI.

## Decisions already made

| Decision | Choice | Why |
|---|---|---|
| Scope | A first, then decide on B | A answers most of the gate cheaply; B's value depends on which stage fails. |
| Build | Three calls through the existing loop | Reuses budgets, events, records and accounting per stage; each stage visible and testable. One call with stage fields cannot attribute; a framework (LangGraph) would own the handoff the gate asks to explain — a possible later port, measured against this baseline. |
| Budget | ≤ $1.50 new spend | The plain-agent side already exists (Phase 05, `agent` arm, same slice, 3 repeats). |
| Live UI | Not in A | `/runs` keeps the single agent; staged runs are eval-only until Piece C. |
| Parallel branches (p6c) | Not in A | They would double locate's cost; if locate fails most, branches are the first measured fix. |

## 1. Stages and contracts

**Loop change** (`agent/loop.py`): `run(...)` gains `output_model: type[BaseModel] = GroundedTriageResult` and `check: Callable[[BaseModel], None] = check_in_scope`. The output schema sent is `anthropic.transform_schema(output_model)`; the final text is validated against `output_model`; `check` runs on the validated result and raises `ValueError` on violation (turned into `RunError`, as today). Defaults keep every existing caller identical.

**Models** (`api/schema.py`):

- `LocatedFile`: `path`, `why`, `evidence`.
- `LocateResult`: `reasoning`, `files: list[LocatedFile]` (1 to 5 items).
- `Region`: `path`, `symbol: str | None`, `start_line: int`, `end_line: int`,
  `what_it_does`, `relevance`.
- `UnderstandResult`: `reasoning`, `regions: list[Region]` (1 to 6 items).
- `Fault`: `path`, `start_line`, `end_line`, `mechanism`.
- `HypothesisResult(GroundedTriageResult)`: adds `fault: Fault`.
  `suspected_files` is scored exactly as before.

**Stages** (`agent/stages.py`: three system prompts; `agent/pipeline.py`: orchestration):

| Stage | Input message | Tools | Turn cap | Output | Boundary check |
|---|---|---|---|---|---|
| locate | issue + retrieval's 10 candidates (as `grounded_prompt`) | grep, list_dir, read_file, git_log | 8 | `LocateResult` | every path exists at the pin and passes `is_gold_candidate` |
| understand | issue + the located files (path, why) | read_file, grep | 6 | `UnderstandResult` | every region's file is one locate named; `1 <= start_line <= end_line <= file length` |
| hypothesize | issue + `LocateResult` + `UnderstandResult` (JSON) | none | 1 | `HypothesisResult` | `suspected_files` pass `check_in_scope`; `fault.path` is a file of an understand region; fault lines inside the file |

**Prompts.**
- *locate:* find the files the fix would touch and say what you saw. The
  shortlist is what the next stage reads, so favour recall over order.
- *understand:* read those files and name the regions (function, lines) that
  implement the reported behaviour. Do not judge the fault yet.
- *hypothesize:* from the regions given, decide where the fault is and how it
  causes the report, then rank the files and fill the triage fields.

All three keep the existing evidence rules: no invented paths, no claims beyond
what was read.

**Budget across stages.** One `Budget` for the pipeline: tokens, seconds and
dollars are summed across stages. Each stage runs with
`Budget(max_turns=<stage cap>, max_tokens=B.max_tokens - tokens_so_far,
max_seconds=B.max_seconds - seconds_so_far, max_dollars=B.max_dollars -
dollars_so_far)`, where `B` is the pipeline budget and the `*_so_far` values
sum the earlier stages' `Run` totals. A stage that starts with any remaining
limit at or below zero fails at once as a budget stop. Exhausting the
budget, or any stage `RunError`, ends the pipeline and records which stage
failed.

```python
run_pipeline(title, body, candidates, cutoff, *, budget=None, client=None) -> PipelineRun
```

`PipelineRun` holds the three stage results (or `None` after a failure), the
per-stage `Run`s (turns, trace, cost), `failed_stage: str | None` and `error`.
`PipelineError(RunError)` carries the partial `PipelineRun` and `stage`.

## 2. Gold regions and attribution

**Gold regions** (`eval/gold_regions.py`, free, cached in gitignored
`data/gold_regions.jsonl`):

1. A row's fix commits are those in `data/raw/commits.jsonl` whose `pr_refs`
   intersect the row's `linked_prs`, or whose `closes_refs` contain the issue.
2. For each gold file, run `git blame --line-porcelain <pin> -- <file>`. The
   lines blamed to a fix commit are the fix's own lines still present at the
   pin, already in pin line numbers.
3. Group them into contiguous ranges.

A row with no such lines (the fix was deletion-only, or later rewritten) has
`regions: []` and is attributed at file level only. The report counts those
rows.

**Outcome per run**, checked in order, exactly one:

| Outcome | Condition |
|---|---|
| `error:<stage>` | a stage raised (`RunError`, budget, or boundary check) |
| `locate_miss` | no gold file in `LocateResult.files` |
| `understand_miss` | a gold file was located, but no understand region overlaps a gold region (rows without region gold: no region in a gold file) |
| `hypothesize_miss` | understand overlapped a gold region (or, file-level, had a region in a gold file), but `suspected_files[0]` is not gold |
| `hit` | `suspected_files[0]` is gold; `fault_hit` additionally records whether `fault` overlaps a gold region (null when the row has no region gold) |

Ranges are inclusive: two ranges overlap when `a.start <= b.end and b.start <=
a.end`.

## 3. The comparison and the run

- `eval/arms.py` gains arm **`staged`**. `eval/ablation.py`'s `make_record`
  dispatches it to `run_pipeline`. A staged record is the normal record plus:
  - `stages`: each stage's output dict, or null
  - `stage_costs`: per stage
  - `failed_stage`
  - `outcome`, computed at record time from gold regions
  - `fault_hit`
- Records go into the existing `data/runs/val-n40-r3.jsonl`, next to the
  `agent` arm's 120 records, so the existing report pairs staged against agent
  on identical issues. It covers recall@1/@5, MRR, intervals, cost per triage
  and per correct answer, and p50/p95 latency.
- `eval/ablation_report.py` gains an **outcome table** for the staged arm:
  each outcome's share (clean and all, with min–max across repeats), the count
  of rows with file-level-only attribution, and `fault_hit` rate among hits.
  It also prints the stage with the largest miss share.
- **Run:**
  1. Smoke test: 2 issues × 1 repeat, staged arm. Measure cost per issue.
  2. The full run: `--arms staged --n 40 --repeats 3`, with `--max-dollars`
     set to the file's existing spend plus 1.50 (the cap is lifetime per
     file).

## 4. Tests (no paid calls)

- **`tests/test_loop.py` additions:**
  - `output_model` and `check` are honoured
  - the defaults are unchanged
- **`tests/test_stage_contracts.py`:**
  - each model validates
  - each boundary check rejects, naming its stage: path not at the pin;
    understand region outside locate's files; line range out of bounds;
    `fault.path` outside understand's files
- **`tests/test_pipeline.py`** (FakeClient):
  - stage order
  - each stage receives the previous result
  - each stage gets only its own tools; hypothesize sends no `tools` key
  - budget totals carry across stages
  - exhaustion is attributed to the right stage
  - an understand `RunError` gives `failed_stage == "understand"`
- **`tests/test_gold_regions.py`** (real Vite clone):
  - issue #13907 (fix #13909) yields at least one region in
    `packages/vite/src/node/plugins/esbuild.ts`, with lines that read back
    from the pinned file
  - a synthetic deletion-only case yields `[]`
- **`tests/test_attribution.py`:**
  - each outcome on hand-made records
  - ranges touching at one line overlap
  - the file-level fallback when regions are empty
- **`tests/test_ablation_report.py` additions:**
  - the outcome table
  - "fails most" picks the largest miss share
  - the staged arm appears in paired comparisons

## 5. Docs

- **`MEASUREMENTS.md`** (append-only), a "Phase 06a" section:
  - staged vs agent, with intervals and cost
  - the outcome table
  - the gate sentence: which stage fails most and what to fix first
- **`ARCHITECTURE.md`:** the pipeline, the stage contracts, gold regions, and
  attribution.
- **`README.md`:** the Phase 06 status row.

## Out of scope

- Patch and verify (Piece B).
- The UI (Piece C).
- Parallel branches.
- A 206-row confirmation (out of budget; a follow-up).
- Staged runs through `/runs`.
- LangGraph.

## Risks

- **Cost per issue may exceed $0.008.** The smoke test measures it, and the
  lifetime cap stops the run cleanly.
- **Some fixes have no surviving lines at the pin.** Those rows fall back to
  file-level attribution, and the count is reported.
- **Hypothesize sees only what understand passed.** That is intended for
  attribution, but it may lower accuracy against the single agent. The
  comparison shows whether it does.
- **40 rows is small.** Outcome shares carry min–max across repeats, and the
  write-up says what the sample cannot separate.
