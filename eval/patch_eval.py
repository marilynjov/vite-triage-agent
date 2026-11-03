"""Patch every stored staged hypothesis on a harness-valid issue, then verify it.

    PROVIDER=anthropic python -m eval.patch_eval [--issues 1,2] [--max-dollars 0.55]

One record per source record (arm "staged" with a hypothesis, harness-valid
issue), appended and flushed to data/runs/val-n40-patch.jsonl as it finishes. The
file is the resume state: a rerun skips the keys already there and retries only
infrastructure failures (error_kind "api" or "infra"), which say nothing about
the patch. Outcomes: stale_path (the hypothesis's file does not exist before
the fix; no model call), patch_error (the stage raised), pass / test_fail
(the gold tests), or null with an error_kind (not counted).
"""
from __future__ import annotations

import argparse
import json
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import NamedTuple

import anthropic

from agent.loop import RunError
from agent.patch import patch_to_diff, run_patch
from api import config as C
from eval import container
from eval.ablation import RUNS_DIR, load_records, val_rows
from eval.fixes import Fix, _git, fix_for
from eval.verify import verify
from eval.verify_harness import load_commits, load_harness

STAGED_FILES = (RUNS_DIR / "val-n40-r3.jsonl", RUNS_DIR / "val-unit-staged-r1.jsonl")
OUT = RUNS_DIR / "val-n40-patch.jsonl"
PRIOR_COST = 0.01  # dollars per patch until a real record exists
RETRY_KINDS = ("api", "infra")
ALL = 10**6


class PatchJob(NamedTuple):
    issue: int
    repeat: int
    row: dict
    source: dict
    fix: Fix


def path_exists(sha: str, path: str) -> bool:
    return _git("cat-file", "-e", f"{sha}:{path}").returncode == 0


def eligible(staged: list[dict], harness: dict[int, dict], rows: dict[int, dict],
             fixes: dict[int, Fix]) -> list[PatchJob]:
    """Staged records with a hypothesis on a harness-valid issue, one job each."""
    seen: set[tuple[int, int]] = set()
    jobs = []
    for r in staged:
        if r.get("arm") != "staged":
            continue
        key = (r["issue"], r["repeat"])
        if key in seen:
            raise ValueError(f"duplicate staged record for (issue, repeat) = {key}")
        seen.add(key)
        if not (r.get("stages") or {}).get("hypothesis"):
            continue
        if not harness.get(r["issue"], {}).get("valid"):
            continue
        if r["issue"] not in rows or r["issue"] not in fixes:
            continue
        jobs.append(PatchJob(r["issue"], r["repeat"], rows[r["issue"]], r, fixes[r["issue"]]))
    return sorted(jobs, key=lambda j: (j.issue, j.repeat))


def load_patch_records(path: Path) -> list[dict]:
    """Records in the file; blank and unparseable lines (a crash mid-write) are skipped."""
    if not Path(path).exists():
        return []
    out = []
    for line in open(path):
        try:
            rec = json.loads(line)
        except ValueError:
            continue
        if isinstance(rec, dict) and {"issue", "repeat"} <= rec.keys():
            out.append(rec)
    return out


def done_keys(records: list[dict]) -> set[tuple[int, int]]:
    return {(r["issue"], r["repeat"]) for r in records
            if r.get("error_kind") not in RETRY_KINDS}


def _cost(run) -> float:
    return float(run.cost_usd) if run is not None else 0.0


def make_patch_record(job: PatchJob, *, patch_fn=run_patch, verify_fn=verify) -> dict:
    """Patch and verify one source record. Never raises: whatever happened is the record."""
    row, fix = job.row, job.fix
    hyp = job.source["stages"]["hypothesis"]
    rec = {"issue": job.issue, "repeat": job.repeat,
           "source_outcome": job.source.get("outcome"),
           "source_fault_hit": job.source.get("fault_hit"), "prefix_sha": fix.prefix_sha,
           "patch": None, "diff": None, "verify": None, "outcome": None,
           "cost_usd": 0.0, "turns": 0, "error": None, "error_kind": None}
    if not path_exists(fix.prefix_sha, hyp["fault"]["path"]):
        rec["outcome"] = "stale_path"
        return rec
    run = None
    try:
        run = patch_fn(row["title"], row["body"], hyp, fix.prefix_sha, row["created_at"])
        rec["cost_usd"], rec["turns"] = _cost(run), len(run.turns)
        rec["patch"] = run.result.model_dump(mode="json")
        rec["diff"] = patch_to_diff(run.result.edits, fix.prefix_sha)
    except RunError as exc:
        part = getattr(exc, "run", None)
        rec["cost_usd"], rec["turns"] = _cost(part), len(part.turns) if part else 0
        rec["error"] = str(exc)
        if isinstance(exc.__cause__, anthropic.APIError):
            rec["error_kind"] = "api"
        else:
            rec["outcome"] = "patch_error"
        return rec
    except anthropic.APIError as exc:
        rec.update(error=f"{type(exc).__name__}: {exc}", error_kind="api")
        return rec
    except Exception as exc:  # not the patch's fault, not a result: retry on resume
        rec.update(error=f"{type(exc).__name__}: {exc}", error_kind="api")
        return rec

    try:
        with container.LOCK:  # prepare is idempotent: a missing base is rebuilt
            ok, tail = container.prepare(fix)
            if not ok:
                rec.update(error=f"prepare failed: {tail}", error_kind="infra")
                return rec
            res = verify_fn(fix, run.result.edits)
    except Exception as exc:
        rec.update(error=f"{type(exc).__name__}: {exc}", error_kind="infra")
        return rec
    rec["verify"] = {"passed": res.passed, "exit_code": res.exit_code, "seconds": res.seconds,
                     "tail": res.tail, "timed_out": res.timed_out, "status": res.status}
    if res.status == "pass":
        rec["outcome"] = "pass"
    elif res.status in ("test_fail", "timeout"):
        rec["outcome"] = "test_fail"
    else:
        rec["error_kind"] = "infra"
        rec["error"] = f"verify status {res.status}: {res.tail[-300:]}"
    return rec


def run_patch_jobs(jobs: list[PatchJob], path: Path, *, cap_dollars: float, workers: int = 4,
                   patch_fn=run_patch, verify_fn=verify) -> dict:
    """Run the jobs on a thread pool, appending each record as one flushed line.

    Once the actual spend passes cap_dollars no new job starts; jobs in flight finish
    and are recorded. Skipped jobs are left for a resume."""
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
        def work(job: PatchJob) -> None:
            with lock:
                if state["spent"] > cap_dollars:
                    state["stopped_for_cap"] = True
                    return
            rec = make_patch_record(job, patch_fn=patch_fn, verify_fn=verify_fn)
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


def estimate_cost(jobs: list[PatchJob], records: list[dict]) -> float:
    """Dollars the pending jobs should cost: stale ones are free; the rest at the measured
    mean of real patches, else the prior."""
    paid = [r["cost_usd"] for r in records
            if r.get("error_kind") is None and r.get("outcome") != "stale_path"]
    unit = sum(paid) / len(paid) if paid else PRIOR_COST
    live = [j for j in jobs
            if path_exists(j.fix.prefix_sha, j.source["stages"]["hypothesis"]["fault"]["path"])]
    return unit * len(live)


def load_inputs() -> tuple[dict[int, dict], dict[int, Fix]]:
    """Val rows by issue, and the fix for every harness-valid one."""
    rows = {r["issue"]: r for r in val_rows("val", ALL)}
    commits = load_commits()
    valid = {i for i, h in load_harness().items() if h.get("valid")}
    fixes = {}
    for i in sorted(valid & rows.keys()):
        fix = fix_for(rows[i], commits)
        if fix:
            fixes[i] = fix
    return rows, fixes


def main(argv: list[str] | None = None) -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--issues", help="comma-separated issue numbers to patch")
    ap.add_argument("--max-dollars", type=float, default=0.55,
                    help="cap on the file's lifetime spend: refuse to start if what it already "
                         "spent plus the estimate exceeds it; stop if the total passes it")
    ap.add_argument("--workers", type=int, default=4)
    args = ap.parse_args(argv)
    if C.PROVIDER != "anthropic":
        sys.exit("the patch stage needs PROVIDER=anthropic")

    staged = [r for p in STAGED_FILES for r in _load_staged(p)]
    rows, fixes = load_inputs()
    jobs = eligible(staged, load_harness(), rows, fixes)
    if args.issues:
        only = {int(x) for x in args.issues.split(",")}
        jobs = [j for j in jobs if j.issue in only]
    existing = load_patch_records(OUT)
    done = done_keys(existing)
    jobs = [j for j in jobs if (j.issue, j.repeat) not in done]

    estimate = estimate_cost(jobs, existing)
    spent_before = sum(r.get("cost_usd") or 0.0 for r in existing)
    print(f"  {OUT}: {len(existing)} records, {len(jobs)} to run")
    print(f"  estimate ${estimate:.2f} + ${spent_before:.2f} already spent in this file = "
          f"${estimate + spent_before:.2f} (cap ${args.max_dollars:.2f} for this file)")
    if spent_before + estimate > args.max_dollars:
        sys.exit(f"estimate ${estimate:.2f} + ${spent_before:.2f} already spent exceeds "
                 f"--max-dollars {args.max_dollars} for this file; raise it or use --issues")
    t0 = time.monotonic()
    out = run_patch_jobs(jobs, OUT, cap_dollars=args.max_dollars - spent_before,
                         workers=args.workers)
    print(f"  ran {out['ran']}   ${out['spent']:.2f}   {time.monotonic() - t0:.0f}s")
    if out["stopped_for_cap"]:
        print(f"  stopped: the file's total spend passed ${args.max_dollars:.2f}; "
              "raise --max-dollars to resume the rest")


def _load_staged(path: Path) -> list[dict]:
    if not Path(path).exists():
        return []
    return load_records(path)


if __name__ == "__main__":
    main()
