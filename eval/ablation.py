"""Does looking pay? Run each ablation arm on the same issues, several times, one record per run.

    PROVIDER=anthropic python -m eval.ablation --arms grounded,agent --split val --n 40 --repeats 3

One run is noisy, so every (arm, issue, repeat) is its own JSON line in
data/runs/<name>.jsonl, appended and flushed as it finishes. That makes the
file the resume state: a rerun skips the keys already there and retries only
infrastructure failures (error_kind "api"), which say nothing about the arm.
A run that breaks the contract (error_kind "run") is a result: it scores zero,
as in production it would be a 500, and it keeps the turns and cost it paid for.

Retrieval runs once per row, and only when one of that row's jobs actually
runs; the model arms get the same candidates, so arms differ only in prompt
and tools. The test split is refused: it is read once, at the end.
"""
from __future__ import annotations

import argparse
import json
import sys
import threading
import time
from dataclasses import replace
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Callable, NamedTuple

from agent.loop import RunError, run
from agent.pipeline import run_pipeline
from api import config as C
from mining import config as M
from retrieval import search

from .arms import ARMS, MODEL_ARMS, Arm
from .attribution import outcome as attribute
from .retrieve import query_for, score_ranking

RUNS_DIR = Path("data/runs")
K_VALUES = [1, 5]
PREVIEW = 2000
# dollars per run, measured in earlier phases; used until an arm has a successful record
PRIOR_COST = {"grounded": 0.0018, "agent-no-tools": 0.0018, "agent": 0.0029,
              "agent-look": 0.0045, "retrieval": 0.0, "staged": 0.010}


class Job(NamedTuple):
    arm: Arm
    row: dict
    repeat: int
    split: str
    candidates: Callable[[], list]  # lazy and cached per row: retrieval is not free


def load_records(path: Path) -> list[dict]:
    """Records in the file; blank and unparseable lines (a crash mid-write) are skipped."""
    if not Path(path).exists():
        return []
    out = []
    for line in open(path):
        try:
            rec = json.loads(line)
        except ValueError:
            continue
        if isinstance(rec, dict) and {"arm", "issue", "repeat"} <= rec.keys():
            out.append(rec)
    return out


def done_keys(records: list[dict]) -> set[tuple[str, int, int]]:
    return {(r["arm"], r["issue"], r["repeat"]) for r in records
            if r.get("error_kind") != "api"}


def make_record(arm: Arm, row: dict, repeat: int, candidates: list, split: str,
                *, run_fn=run, pipeline_fn=run_pipeline, regions: dict | None = None) -> dict:
    """Run one job. Never raises: whatever happened is the record.

    regions maps issue -> {gold_file: [[start, end], ...]}; it is read only by the
    staged arm, and a missing issue counts as no regions (file-level gold)."""
    paths = [getattr(c, "path", c) for c in candidates]
    gold = set(row["gold_files"])
    picked: list[str] = []
    turns: list = []
    trace: list = []
    cost = 0.0
    error = error_kind = None
    pipeline = arm.kind == "pipeline"
    stages: dict = {"locate": None, "understand": None, "hypothesis": None}
    stage_costs: dict = {}
    failed_stage = None
    started = time.perf_counter()
    try:
        if pipeline:
            r = pipeline_fn(row["title"], row["body"], candidates, row["created_at"])
            picked = list(r.result.suspected_files)
            turns, trace, cost = r.turns, r.trace, float(r.cost_usd)
            stages, stage_costs = _stages(r), dict(r.stage_costs)
        elif arm.system is None:
            picked = list(paths)
        else:
            r = run_fn(row["title"], row["body"], candidates, row["created_at"],
                       system=arm.system, tool_specs=arm.tool_specs)
            picked = list(r.result.suspected_files)
            turns, trace, cost = r.turns, r.trace, float(r.cost_usd)
    except RunError as exc:
        error = str(exc)
        error_kind = "api" if error.startswith("API error") else "run"
        turns, trace, cost = exc.run.turns, exc.run.trace, float(exc.run.cost_usd)
        if pipeline:
            failed_stage = getattr(exc, "stage", None)
            stages, stage_costs = _stages(exc.run), dict(exc.run.stage_costs)
    except Exception as exc:  # infrastructure, not the arm's fault
        error, error_kind = f"{type(exc).__name__}: {exc}", "api"
    latency_ms = int((time.perf_counter() - started) * 1000)
    scores = score_ranking(picked, gold, K_VALUES)
    rec = {
        "arm": arm.name, "repeat": repeat, "issue": row["issue"],
        "mentions_gold_path": row["mentions_gold_path"], "gold": list(row["gold_files"]),
        "candidates": paths, "picked": picked,
        "recall_at_1": scores["recall@1"], "recall_at_5": scores["recall@5"],
        "rr": scores["mrr"], "turns": len(turns),
        "tool_calls": [{"name": t.name, "input": t.input, "is_error": t.is_error,
                        "output_preview": t.output[:PREVIEW]} for t in trace],
        "input_tokens": sum(t.input_tokens for t in turns),
        "output_tokens": sum(t.output_tokens for t in turns),
        "cost_usd": cost, "latency_ms": latency_ms,
        "error": error, "split": split, "error_kind": error_kind,
    }
    if pipeline:
        gold_regions = (regions or {}).get(row["issue"]) or {}
        if error_kind == "api" and failed_stage is None:  # infrastructure: nothing to attribute
            out, fault_hit = None, None
        else:
            out, fault_hit = attribute(stages, failed_stage, list(row["gold_files"]),
                                       gold_regions)
        rec.update({
            "stages": stages, "stage_costs": stage_costs, "failed_stage": failed_stage,
            "outcome": out, "fault_hit": fault_hit,
            # stored so the report stays pure: every gold file is judged at file level only
            "file_level_only": all(not gold_regions.get(g) for g in row["gold_files"]),
        })
    return rec


def _stages(run) -> dict:
    """The stage outputs that completed, as JSON-safe dicts (None for the rest)."""
    return {k: (m.model_dump(mode="json") if m is not None else None)
            for k, m in (("locate", run.locate), ("understand", run.understand),
                         ("hypothesis", run.hypothesis))}


def estimate_cost(jobs: list[Job], records: list[dict]) -> float:
    """Dollars the pending jobs should cost: per arm, measured mean if any, else the prior."""
    total = 0.0
    for name in {j.arm.name for j in jobs}:
        paid = [r["cost_usd"] for r in records
                if r["arm"] == name and r.get("error_kind") is None]
        unit = sum(paid) / len(paid) if paid else PRIOR_COST[name]
        total += unit * sum(1 for j in jobs if j.arm.name == name)
    return total


def run_jobs(jobs: list[Job], path: Path, *, workers: int = 4,
             cap_dollars: float | None = None, run_fn=run,
             pipeline_fn=run_pipeline, regions: dict | None = None) -> dict:
    """Run the jobs on a thread pool, appending each record as one flushed line.

    Once the actual spend passes cap_dollars no new job starts; jobs already in
    flight finish and are recorded. Skipped jobs are left for a resume."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    lock = threading.Lock()
    state = {"ran": 0, "spent": 0.0, "stopped_for_cap": False}

    if path.exists() and path.stat().st_size:
        with open(path, "rb") as f:
            f.seek(-1, 2)
            torn = f.read(1) != b"\n"
        if torn:  # a crash left a partial line; do not glue the next record onto it
            with open(path, "a") as f:
                f.write("\n")

    with open(path, "a") as fh, ThreadPoolExecutor(workers) as pool:
        def work(job: Job) -> None:
            with lock:
                if cap_dollars is not None and state["spent"] > cap_dollars:
                    state["stopped_for_cap"] = True
                    return
            try:
                candidates = job.candidates()
            except Exception as exc:  # retrieval failed: an infrastructure record, not a crash
                def fail(*a, **k):
                    raise exc
                rec = make_record(replace(job.arm, system=job.arm.system or ""), job.row,
                                  job.repeat, [], job.split, run_fn=fail, pipeline_fn=fail,
                                  regions=regions)
            else:
                rec = make_record(job.arm, job.row, job.repeat, candidates, job.split,
                                  run_fn=run_fn, pipeline_fn=pipeline_fn, regions=regions)
            with lock:
                fh.write(json.dumps(rec) + "\n")
                fh.flush()
                state["ran"] += 1
                state["spent"] += rec["cost_usd"]
                print(f"\r  {state['ran']}/{len(jobs)}  ${state['spent']:.2f}",
                      end="", flush=True)

        for f in [pool.submit(work, j) for j in jobs]:
            f.result()
    if jobs:
        print()
    return state


def pending_jobs(arms: list[Arm], rows: list[dict], repeats: int, split: str,
                 done: set[tuple[str, int, int]]) -> list[Job]:
    """Jobs not yet done, built from the rows alone: retrieval waits until a job runs."""
    jobs = []
    for row in rows:
        lock = threading.Lock()
        cache: list = []

        def candidates(row=row, lock=lock, cache=cache):
            with lock:
                if not cache:
                    cache.append(search.search_ranked(
                        query_for(row), strategy="fixed_v1_header", k=C.GROUNDED_K))
                return cache[0]

        for arm in arms:
            for rep in range(repeats):
                if (arm.name, row["issue"], rep) not in done:
                    jobs.append(Job(arm, row, rep, split, candidates))
    return jobs


def val_rows(split: str, n: int) -> list[dict]:
    """The same slice eval.agent uses: sorted by issue, first n."""
    rows = [json.loads(l) for l in open(M.SPLIT_DIR / f"{split}.jsonl")]
    return sorted(rows, key=lambda r: r["issue"])[:n]


def main(argv: list[str] | None = None) -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--arms", default=",".join(MODEL_ARMS))
    ap.add_argument("--split", default="val")
    ap.add_argument("--n", type=int, default=40)
    ap.add_argument("--repeats", type=int, default=3)
    ap.add_argument("--name")
    ap.add_argument("--report", metavar="PATH",
                    help="print the report for an existing run file and exit")
    ap.add_argument("--max-dollars", type=float, default=3.0,
                    help="cap on the run file's lifetime spend: refuse to start if what the "
                         "file already spent plus the estimate exceeds it; stop if the total passes it")
    args = ap.parse_args(argv)
    if args.report:
        from . import ablation_report
        ablation_report.main(args.report)
        return
    if args.split == "test":
        sys.exit("refusing the test split: it is read once, at the end")
    names = args.arms.split(",")
    unknown = [a for a in names if a not in ARMS]
    if unknown:
        sys.exit(f"unknown arms {unknown}; choose from {list(ARMS)}")

    path = RUNS_DIR / f"{args.name or f'{args.split}-n{args.n}-r{args.repeats}'}.jsonl"
    existing = load_records(path)
    other = {r.get("split") for r in existing} - {args.split}
    if other:
        sys.exit(f"{path} holds records from split {sorted(map(str, other))}; use another --name")
    if any(ARMS[a].system is not None or ARMS[a].kind == "pipeline" for a in names) and C.PROVIDER != "anthropic":
        sys.exit("model arms need PROVIDER=anthropic")

    rows = val_rows(args.split, args.n)
    regions = None
    if any(ARMS[a].kind == "pipeline" for a in names):  # once per run, only for the staged arm
        from .gold_regions import load_regions
        regions = load_regions(rows)
    jobs = pending_jobs([ARMS[a] for a in names], rows,
                        args.repeats, args.split, done_keys(existing))
    estimate = estimate_cost(jobs, existing)
    # every record was paid for, errors included; the cap bounds the file, not this invocation
    spent_before = sum(r.get("cost_usd") or 0.0 for r in existing)
    print(f"  {path}: {len(existing)} records, {len(jobs)} to run")
    print(f"  estimate ${estimate:.2f} + ${spent_before:.2f} already spent in this file = "
          f"${estimate + spent_before:.2f} (cap ${args.max_dollars:.2f} for this file)")
    if spent_before + estimate > args.max_dollars:
        sys.exit(f"estimate ${estimate:.2f} + ${spent_before:.2f} already spent exceeds "
                 f"--max-dollars {args.max_dollars} for this file; "
                 "raise it or shrink --n/--repeats/--arms")
    out = run_jobs(jobs, path, cap_dollars=args.max_dollars - spent_before, regions=regions)
    print(f"  ran {out['ran']}   ${out['spent']:.2f}")
    if out["stopped_for_cap"]:
        print(f"  stopped: the file's total spend passed ${args.max_dollars:.2f}; "
              "raise --max-dollars to resume the rest")


if __name__ == "__main__":
    main()
