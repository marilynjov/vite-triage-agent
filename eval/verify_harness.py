"""Prove the harness before spending on models.

    python -m eval.verify_harness [--issues 14515,...]

For each unit-tested val issue: the real fix's gold tests must FAIL on the
pre-fix code and PASS with the real fix applied. Only issues that do both are
valid; later phases grade model patches on those alone. One record per issue is
appended to data/verify_harness.jsonl, which is also the cache.
"""
from __future__ import annotations

import argparse
import json
import sys
import time

from eval.ablation import val_rows
from eval.container import discard, ensure_image, prepare, run_tests
from eval.fixes import Fix, file_at, fix_for
from mining import config as M

DATA = M.DATA
ALL = 10**6


def _path():
    return DATA / "verify_harness.jsonl"


def validate(fix: Fix, *, prepare_fn=prepare, run_fn=run_tests) -> dict:
    rec = {"issue": fix.issue, "valid": False, "reason": None,
           "prefix_sha": fix.prefix_sha, "fix_sha": fix.fix_sha,
           "fail_tail": "", "pass_tail": "", "seconds": 0.0}

    ok, tail = prepare_fn(fix)
    if not ok:
        rec.update(reason="install_failed", fail_tail=tail)
        return rec

    tests = {p: file_at(fix.fix_sha, p) for p in fix.gold_tests}
    before = run_fn(fix, tests, ())
    rec.update(fail_tail=before.tail, seconds=before.seconds)
    if before.status != "test_fail":
        rec["reason"] = {"pass": "passes_without_fix", "timeout": "timeout"}.get(
            before.status, "infra_error")
        return rec

    files = dict(tests)
    files.update({p: file_at(fix.fix_sha, p) for p in fix.fix_source})
    after = run_fn(fix, files, fix.fix_deleted)
    rec.update(pass_tail=after.tail, seconds=before.seconds + after.seconds)
    if after.status == "pass":
        rec["valid"] = True
    else:
        rec["reason"] = {"test_fail": "fails_with_fix", "timeout": "timeout"}.get(
            after.status, "infra_error")
    return rec


def load_harness() -> dict[int, dict]:
    path = _path()
    if not path.exists():
        return {}
    out = {}
    for n, line in enumerate(path.read_text().splitlines(), 1):
        if not line.strip():
            continue
        try:
            rec = json.loads(line)
            out[rec["issue"]] = rec  # a later record supersedes an earlier one
        except (json.JSONDecodeError, KeyError, TypeError):
            print(f"verify_harness: skipping unreadable line {n} of {path}", file=sys.stderr)
    return out


def candidate_rows() -> list[dict]:
    return val_rows("val", ALL)


def baseline_ids() -> set[int]:
    return {r["issue"] for r in val_rows("val", 40)}


def load_commits() -> list[dict]:
    return [json.loads(l) for l in (M.RAW / "commits.jsonl").read_text().splitlines() if l]


def _append(rec: dict) -> None:
    DATA.mkdir(parents=True, exist_ok=True)
    path = _path()
    torn = False
    if path.exists() and path.stat().st_size:
        with open(path, "rb") as f:
            f.seek(-1, 2)
            torn = f.read(1) != b"\n"
    with open(path, "a") as f:
        if torn:
            f.write("\n")
        f.write(json.dumps(rec) + "\n")
        f.flush()


def _discard(fix: Fix, discard_fn) -> None:
    """Free the issue's base (2-3 GB) once its record is written; a failure is reported,
    not fatal: the record stands, and the next prepare will show if Docker is broken."""
    try:
        discard_fn(fix)
    except Exception as e:
        print(f"verify_harness: discard {fix.issue} failed: {type(e).__name__}: {e}",
              file=sys.stderr)


def main(argv: list[str] | None = None, *, discard_fn=None) -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--issues", help="comma-separated issue numbers to validate")
    args = ap.parse_args(argv)
    only = {int(x) for x in args.issues.split(",")} if args.issues else None

    commits = load_commits()
    fixes = []
    for row in candidate_rows():
        if only is not None and row["issue"] not in only:
            continue
        fix = fix_for(row, commits)
        if fix:
            fixes.append(fix)

    cached = load_harness()
    def hit(f: Fix) -> bool:
        r = cached.get(f.issue)
        return bool(r) and r.get("reason") != "infra_error" and \
            (r.get("prefix_sha"), r.get("fix_sha")) == (f.prefix_sha, f.fix_sha)

    todo = [f for f in fixes if not hit(f)]
    if todo:
        ensure_image()
    for fix in todo:
        t0 = time.monotonic()
        try:
            rec = validate(fix)
        except Exception as e:  # one broken issue must not lose the others
            rec = {"issue": fix.issue, "valid": False, "reason": "infra_error",
                   "prefix_sha": fix.prefix_sha, "fix_sha": fix.fix_sha,
                   "fail_tail": f"{type(e).__name__}: {e}", "pass_tail": "",
                   "seconds": time.monotonic() - t0}
        try:
            _append(rec)
            cached[fix.issue] = rec
        finally:  # one base on disk at a time, even if the append itself fails
            _discard(fix, discard_fn or discard)

    print(f"{'issue':>7}  {'valid':5}  {'reason':20}  seconds")
    for fix in fixes:
        r = cached[fix.issue]
        print(f"{r['issue']:>7}  {str(r['valid']):5}  {r.get('reason') or '-':20}  "
              f"{r.get('seconds', 0):.0f}")

    valid = sorted(i for i, r in cached.items() if r["valid"])
    base = baseline_ids()
    DATA.mkdir(parents=True, exist_ok=True)
    (DATA / "verify_valid.txt").write_text("".join(f"{i}\n" for i in valid))
    (DATA / "verify_valid_new.txt").write_text("".join(f"{i}\n" for i in valid if i not in base))
    print(f"{len(valid)} valid in cache")


if __name__ == "__main__":
    main(sys.argv[1:])
