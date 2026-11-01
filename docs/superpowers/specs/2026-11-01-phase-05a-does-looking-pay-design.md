# Phase 05, Piece A — Does looking pay? Design

Date: 2026-11-01 · Status: approved in conversation, awaiting spec review

## Purpose

Phase 05's gate is an honest A/B: agent against retrieval-only, on quality
*and* cost, where "agency didn't pay for itself" is a valid answer. Phase 04
left that question open in a specific way:

- The agent beat grounded triage on the 40-row slice (clean recall@1 41.5%
  against 30.4%), but **26 of 40 runs called no tools**. The lift could be the
  agent's prompt, not looking.
- **Runs vary.** The same issue (#14009) hit in one run and missed in the next,
  and grounded's own recall@1 moved 3 points between two identical runs.
- **The model doesn't look when it should.** On a private-repo issue it wrote
  that the spec it needed was "not included" and did not read it, though
  `read_file` could have.

Piece A answers "does looking pay?" by separating the prompt's effect from the
tools' effect, measuring run-to-run variation, and testing one prompt nudge,
then confirming the result on all 206 val rows.

Phase 05 is split into three pieces, each with its own spec, plan and build:
**A** (this spec), **B** budgets and the SSE event stream, **C** the live trace
UI. A comes first because its answer shapes B.

## Decisions already made

| Decision | Choice | Why |
|---|---|---|
| Order | Piece A before B and C | Measure whether looking helps before building infrastructure around it. |
| Method | Ablation arms (A1) | The only option that separates prompt from tools causally. Splitting rows by tool use is biased (the model looks on hard rows); forcing a tool call skips thinking on Haiku 5.5, which changes two things at once. |
| Budget | Tune on 40 rows × 3 repeats, confirm on all 206 rows × 1 | About $2.50 total. The headline comes from 206 rows. |
| Model, data | `claude-haiku-5-5`, Vite val | Same as Phase 04. The test split stays sealed until Phase 07. |
| Tools | The four Phase 04 tools, unchanged | Changing the toolset would confound "does looking pay?". Per-tool usage is recorded to inform later changes. |
| "Not a code change" answer | Out of scope | Every val issue has a code fix by construction, so val cannot measure it. |

## 1. The arms

An **arm** is one way of running triage. Every arm gets the same issues and the
same 10 retrieved candidates (retrieval runs once per issue), and is scored the
same way. Adjacent arms differ in one thing.

| Arm | `system` | `tool_specs` | Question its neighbour answers |
|---|---|---|---|
| `grounded` | `GROUNDED_SYSTEM` | `[]` | baseline |
| `agent-no-tools` | `AGENT_SYSTEM` | `[]` | grounded → here: the prompt alone |
| `agent` | `AGENT_SYSTEM` | `TOOL_SPECS` | no-tools → here: the tools alone |
| `agent-look` | `AGENT_SYSTEM + LOOK_FIRST` | `TOOL_SPECS` | agent → here: the nudge |
| `retrieval` | none (no model call) | — | the roadmap gate's baseline; free |

`LOOK_FIRST` is appended to `AGENT_SYSTEM`:

> If your conclusion depends on code or documents you cannot see in the
> excerpts, use a tool to check it before you conclude. Name a file outside the
> candidates only after a tool showed that it exists and fits the report.

## 2. One loop for every arm — `agent/loop.py`, `eval/arms.py`

All model arms run through `agent.loop.run`, so the arms differ only in prompt
and tools, never in plumbing. `run` gains two keyword parameters with defaults
that keep `/triage/agent` and every existing test unchanged:

```python
run(title, body, candidates, cutoff, *, system=AGENT_SYSTEM,
    tool_specs=tools.TOOL_SPECS, max_turns=None, client=None) -> Run
```

- With `tool_specs=[]` the request **omits `tools`** entirely. The model can
  only answer, so the run is one turn with the same `output_config` schema.
- `eval/arms.py` holds the arms as one table: name → (system, tool_specs), plus
  the `retrieval` pseudo-arm. `LOOK_FIRST` lives there.
- Known difference, accepted: arm `grounded` is not byte-identical to Phase 04's
  `/triage/grounded`, which uses `messages.parse` and a candidates-only check.
  Here every arm uses the loop and `check_in_scope`. Its numbers may differ
  slightly from Phase 04's grounded result; the write-up says so.

## 3. The runner — `eval/ablation.py`

```
python -m eval.ablation --arms grounded,agent-no-tools,agent,agent-look \
    --split val --n 40 --repeats 3 [--name NAME] [--max-dollars 3]
```

- **Rows:** the same fixed slice as `eval.grounded` / `eval.agent` (lowest issue
  numbers first). Retrieval runs once per issue (`fixed_v1_header`, k =
  `GROUNDED_K`), and all arms and repeats share that candidate list.
- **Cutoff:** each run passes `cutoff=row["created_at"]` to `git_log`.
- **Run records:** one JSON line per (arm, issue, repeat) in
  `data/runs/<name>.jsonl`. `data/runs/` is added to `.gitignore`. Fields:
  `arm`, `repeat`, `issue`, `mentions_gold_path`, `gold`, `candidates`,
  `picked`, `recall_at_1`, `recall_at_5`, `rr`, `turns`, `tool_calls` (list of
  `{name, input, is_error, output_preview}`), `input_tokens`, `output_tokens`,
  `cost_usd`, `latency_ms`, `error` (null on success). A failed run records its
  error and the turns it paid for, and scores zero.
- **Resume:** combinations already present in the file are skipped. A crash, a
  rate limit or a spend stop wastes nothing, and an arm can be added later
  without re-running the others.
- **Concurrency:** a pool of 4 workers. About 480 runs take roughly 20 minutes.
  SDK retries handle transient errors; a failure after retries is a recorded
  error, never a crash.
- **Spend control:**
  - Before starting, estimate cost = remaining runs × each arm's mean cost per
    run (from existing records for that arm, else Phase 04's measured cost:
    grounded/no-tools $0.0018, agent $0.0029, agent-look $0.0045 as a
    deliberate overestimate). Refuse to start if the estimate exceeds
    `--max-dollars` (default 3).
  - While running, sum actual cost from each run's tokens. Stop cleanly, after
    in-flight runs finish, once the cap is passed.
- `retrieval` records are written with zero cost and no model call.

## 4. The report — `python -m eval.ablation --report data/runs/<name>.jsonl`

Reads only the run records; free and repeatable. Every metric is shown for
`all` and `clean` (excluding `mentions_gold_path`); clean is the one quoted.

1. **Per-arm scores:** recall@1, recall@5 and MRR, averaged over repeats, with
   the min–max of the per-repeat scores, e.g. `41 (39–44)`.
2. **Paired comparisons** for `grounded→agent-no-tools`,
   `agent-no-tools→agent`, `agent→agent-look`, and `grounded` vs every agent
   arm. Per issue, each arm's mean over its repeats; then:
   - wins / losses / ties
   - a 95% bootstrap interval for the mean difference: resample issues with
     replacement, 10,000 times, with a fixed seed. Pure Python, no new
     dependency. A difference **counts only if the interval excludes zero**.
3. **Flip rate:** the share of issues where an arm's rank-1 hit differs across
   repeats.
4. **Tool behaviour** (tool arms): the share of runs using any tool, the turn
   distribution, calls per tool, the tool-call error rate, and **picks the
   tools saw** (the share of picked files that appear in some tool output
   during the run). The last is descriptive only, not causal.
5. **Cost:** cost per triage, p50/p95 latency, and **cost per correct answer**
   (total cost ÷ rank-1 hits). Failed runs and contract violations are counted
   as zero and as paid.
6. **Decision rule**, fixed before any paid run: the arm to confirm is the
   agent arm with the highest clean recall@1 whose paired interval against
   `grounded` excludes zero. If none qualifies, confirm `grounded`, and the
   headline is that agency did not pay for itself, with cost per correct answer
   as evidence.
7. A ready-to-paste Markdown block for `MEASUREMENTS.md`.

## 5. The confirming run

```
python -m eval.ablation --arms retrieval,grounded,<winner> --split val --n 206 --repeats 1
```

One repeat: the precision comes from 175 clean rows, and the paired interval
against grounded is computed over them. The 40-row stage has already measured
run-to-run variation. Same records, same report.

## 6. Tests (no paid calls)

- `tests/test_arms.py`: each arm passes the right `system` and `tool_specs` to
  `run`; with `tool_specs=[]` the request has no `tools` key and the run ends
  after one turn (FakeClient, as in `tests/test_loop.py`).
- `tests/test_ablation.py`:
  - a record has every field above; a failed run records its error and turns
    and scores zero
  - resume skips existing (arm, issue, repeat) combinations
  - the spend cap refuses to start when the estimate is over, and stops a run
    mid-way when actual cost passes it
- `tests/test_ablation_report.py`, on hand-made records:
  - means and min–max spread
  - wins/losses/ties
  - the bootstrap interval, deterministic with the seed; an interval that
    excludes zero for a clear gap and includes it for no gap
  - flip rate and cost per correct answer
  - the decision rule: picks the best qualifying agent arm, and falls back to
    `grounded` when none qualifies

## 7. Docs and git

- `MEASUREMENTS.md`, append-only: one section for the 40-row ablation and one
  for the 206-row confirmation, each with the mechanism behind every finding
  and the caveats (arm `grounded` vs Phase 04's grounded; post-fix code; sample
  size).
- `ARCHITECTURE.md`: the eval section gains the arms table and the run-record
  format.
- Git: a branch for Piece A, merged with a PR at the end. Commits carry no
  attribution trailer.

## Out of scope

- Budgets on the agent itself and the SSE event stream (Piece B).
- The live trace UI (Piece C).
- New or changed tools.
- A "not a code change" answer.
- The test split and the private second corpus.

## Risks

- **Arm `grounded` differs slightly from Phase 04's grounded** (see §2).
  Accepted for a fair comparison, and stated in the write-up.
- **Rate limits** at 4 workers. SDK retries plus resume.
- **`agent-look` may cost much more.** That is a result, not a bug. The spend
  cap bounds it.
- **40 rows can still be noisy** even with repeats; the confirmation on 206
  rows is what the headline rests on.
