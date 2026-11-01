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
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Callable, NamedTuple

from agent.loop import RunError, run
from api import config as C
from mining import config as M
from retrieval import search

from .arms import ARMS, MODEL_ARMS, Arm
from .retrieve import query_for, score_ranking

RUNS_DIR = Path("data/runs")
K_VALUES = [1, 5]
PREVIEW = 2000


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
            out.append(json.loads(line))
        except ValueError:
            continue
    return out


def done_keys(records: list[dict]) -> set[tuple[str, int, int]]:
    return {(r["arm"], r["issue"], r["repeat"]) for r in records
            if r.get("error_kind") != "api"}


def make_record(arm: Arm, row: dict, repeat: int, candidates: list, split: str,
                *, run_fn=run) -> dict:
    """Run one job. Never raises: whatever happened is the record."""
    paths = [getattr(c, "path", c) for c in candidates]
    gold = set(row["gold_files"])
    picked: list[str] = []
    turns: list = []
    trace: list = []
    cost = 0.0
    error = error_kind = None
    started = time.perf_counter()
    try:
        if arm.system is None:
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
    except Exception as exc:  # infrastructure, not the arm's fault
        error, error_kind = f"{type(exc).__name__}: {exc}", "api"
    latency_ms = int((time.perf_counter() - started) * 1000)
    scores = score_ranking(picked, gold, K_VALUES)
    return {
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


def run_jobs(jobs: list[Job], path: Path, *, workers: int = 4, run_fn=run) -> dict:
    """Run the jobs on a thread pool, appending each record as one flushed line."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    lock = threading.Lock()
    state = {"ran": 0, "spent": 0.0}

    with open(path, "a") as fh, ThreadPoolExecutor(workers) as pool:
        def work(job: Job) -> None:
            rec = make_record(job.arm, job.row, job.repeat, job.candidates(), job.split,
                              run_fn=run_fn)
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
    args = ap.parse_args(argv)
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
    if any(ARMS[a].system is not None for a in names) and C.PROVIDER != "anthropic":
        sys.exit("model arms need PROVIDER=anthropic")

    jobs = pending_jobs([ARMS[a] for a in names], val_rows(args.split, args.n),
                        args.repeats, args.split, done_keys(existing))
    print(f"  {path}: {len(existing)} records, {len(jobs)} to run")
    out = run_jobs(jobs, path)
    print(f"  ran {out['ran']}   ${out['spent']:.2f}")


if __name__ == "__main__":
    main()
