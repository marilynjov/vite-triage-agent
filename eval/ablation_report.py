"""Turn ablation run records into the numbers that answer "does looking pay?".

    python -m eval.ablation --report data/runs/val-n40-r3.jsonl

Conventions, fixed before the data came in:
- error_kind "api" records (infrastructure) are excluded from every score and
  stat; they appear only as a count in cost_stats. "run" errors score zero and
  their cost counts. Cost note: api-error records are excluded from cost too, so
  paid turns inside an api-error run are not in $/triage (the runtime cap counts them).
- "clean" = issues whose text does not name a gold path; "all" = every record.
- An arm's score is the mean over repeats of the per-repeat metric mean,
  reported with the min and max repeat.
- Comparisons are paired by issue (repeats averaged within an issue) with a
  percentile bootstrap over issues: 10,000 resamples, seed 0, 95% interval.
  A difference counts only if the interval excludes zero.
- Percentiles (bootstrap and latency) use linear interpolation, the numpy default.
- choose_arm ties on clean recall@1 go to the first arm in AGENT_ARMS order.
- Weighting: scores weight repeats equally (mean of per-repeat means); paired
  comparisons weight issues equally (repeats averaged within an issue first).
"""
from __future__ import annotations

import random
import statistics
import sys
from collections import defaultdict
from pathlib import Path

from .ablation import load_records
from .arms import MODEL_ARMS, PIPELINE_ARMS

METRICS = ("recall_at_1", "recall_at_5", "rr")
RETRIEVAL = "retrieval"
BASELINE = MODEL_ARMS[0]
AGENT_ARMS = tuple(a for a in MODEL_ARMS if a != BASELINE)
CHAIN = tuple(zip(MODEL_ARMS, MODEL_ARMS[1:]))
EPS = 1e-12
STAGED = PIPELINE_ARMS[0]
OUTCOME_ORDER = ("hit", "hypothesize_miss", "understand_miss", "locate_miss")


def _use(records, arm, subset=None):
    """Records of the arm that count: api errors out, and the clean filter if asked."""
    if subset not in (None, "all", "clean"):
        raise ValueError(f"subset must be 'all' or 'clean', not {subset!r}")
    return [r for r in records
            if r["arm"] == arm and r.get("error_kind") != "api"
            and (subset != "clean" or not r["mentions_gold_path"])]


def arm_scores(records, arm, subset):
    """metric -> (mean of per-repeat means, min, max); empty dict when no records."""
    recs = _use(records, arm, subset)
    if not recs:
        return {}
    out = {}
    for m in METRICS:
        by_rep = defaultdict(list)
        for r in recs:
            by_rep[r["repeat"]].append(r[m])
        means = [sum(v) / len(v) for v in by_rep.values()]
        out[m] = (sum(means) / len(means), min(means), max(means))
    return out


def _per_issue(records, arm, metric, subset):
    by_issue = defaultdict(list)
    for r in _use(records, arm, subset):
        by_issue[r["issue"]].append(r[metric])
    return {i: sum(v) / len(v) for i, v in by_issue.items()}


def _percentile(sorted_vals, q):
    """Linear-interpolation percentile, q in [0, 100]."""
    pos = (len(sorted_vals) - 1) * q / 100
    lo = int(pos)
    hi = min(lo + 1, len(sorted_vals) - 1)
    return sorted_vals[lo] + (sorted_vals[hi] - sorted_vals[lo]) * (pos - lo)


def paired(records, a, b, *, metric="recall_at_1", subset="clean",
           n_boot=10_000, seed=0):
    """b versus a over the issues both arms have, or None if there are none."""
    va = _per_issue(records, a, metric, subset)
    vb = _per_issue(records, b, metric, subset)
    shared = sorted(va.keys() & vb.keys())
    if not shared:
        return None
    diffs = [vb[i] - va[i] for i in shared]
    n = len(diffs)
    rng = random.Random(seed)
    means = sorted(sum(diffs[rng.randrange(n)] for _ in range(n)) / n
                   for _ in range(n_boot))
    return {"n": n,
            "wins": sum(d > EPS for d in diffs),
            "losses": sum(d < -EPS for d in diffs),
            "ties": sum(abs(d) <= EPS for d in diffs),
            "diff": sum(diffs) / n,
            "ci_low": _percentile(means, 2.5), "ci_high": _percentile(means, 97.5)}


def flip_rate(records, arm, subset):
    """Share of issues with >=2 repeats whose rank-1 hit is not the same every time."""
    hits = defaultdict(list)
    for r in _use(records, arm, subset):
        hits[r["issue"]].append(r["recall_at_1"] > 0)
    multi = [h for h in hits.values() if len(h) >= 2]
    if not multi:
        return None
    return sum(len(set(h)) > 1 for h in multi) / len(multi)


def tool_stats(records, arm):
    recs = _use(records, arm)
    if not recs:
        return {}
    calls = [c for r in recs for c in r["tool_calls"]]
    by_tool = defaultdict(int)
    for c in calls:
        by_tool[c["name"]] += 1
    picks = seen = 0
    for r in recs:
        blob = "\n".join(c["output_preview"] for c in r["tool_calls"])
        for p in r["picked"]:
            picks += 1
            seen += p in blob
    turns = sorted(r["turns"] for r in recs)
    return {"n": len(recs),
            "used_any": sum(bool(r["tool_calls"]) for r in recs) / len(recs),
            "turns_mean": sum(turns) / len(turns),
            "turns_p50": statistics.median(turns), "turns_max": turns[-1],
            "calls_by_tool": dict(by_tool),
            "tool_error_rate": (sum(bool(c["is_error"]) for c in calls) / len(calls)
                                if calls else None),
            "picks_seen": seen / picks if picks else None}


def cost_stats(records, arm):
    """Cost and latency per arm. api-error records are excluded from cost, so paid
    turns inside an api-error run are not counted in $/triage (the runtime cap does)."""
    mine = [r for r in records if r["arm"] == arm]
    api = sum(r.get("error_kind") == "api" for r in mine)
    recs = [r for r in mine if r.get("error_kind") != "api"]
    base = {"run_errors": sum(r.get("error_kind") == "run" for r in recs),
            "api_errors": api}
    if not recs:
        return {**base, "per_triage": None, "per_correct": None,
                "p50_ms": None, "p95_ms": None}
    total = sum(r["cost_usd"] for r in recs)
    hits = sum(r["recall_at_1"] > 0 for r in recs)
    lat = sorted(r["latency_ms"] for r in recs)
    return {**base, "per_triage": total / len(recs),
            "per_correct": total / hits if hits else None,
            "p50_ms": _percentile(lat, 50), "p95_ms": _percentile(lat, 95)}


def choose_arm(records):
    """Best agent arm whose clean recall@1 beats grounded with ci_low > 0, else grounded."""
    best, best_mean = BASELINE, None
    for arm in AGENT_ARMS:
        p = paired(records, BASELINE, arm)
        if p is None or not p["ci_low"] > 0:
            continue
        mean = arm_scores(records, arm, "clean")["recall_at_1"][0]
        if best_mean is None or mean > best_mean:
            best, best_mean = arm, mean
    return best


def _outcome_rows(records, arm, subset):
    return [r for r in _use(records, arm, subset) if r.get("outcome")]


def outcome_table(records, arm=STAGED, subset="clean"):
    """outcome -> (mean share over repeats, min, max); {} without staged records.

    Rows are the arm's non-api records with an outcome (older records lack the key).
    A repeat in which an outcome never occurs counts as share 0."""
    recs = _outcome_rows(records, arm, subset)
    if not recs:
        return {}
    by_rep = defaultdict(list)
    for r in recs:
        by_rep[r["repeat"]].append(r["outcome"])
    seen = {r["outcome"] for r in recs}
    names = [o for o in OUTCOME_ORDER if o in seen] + sorted(seen - set(OUTCOME_ORDER))
    out = {}
    for o in names:
        shares = [v.count(o) / len(v) for v in by_rep.values()]
        out[o] = (sum(shares) / len(shares), min(shares), max(shares))
    return out


def fails_most(records, arm=STAGED):
    """The non-hit outcome with the largest clean mean share, or 'n/a'."""
    misses = {o: v[0] for o, v in outcome_table(records, arm, "clean").items() if o != "hit"}
    if not misses:
        return "n/a"
    return max(misses, key=lambda o: (misses[o], -list(misses).index(o)))


STAGE_OF = {"locate_miss": "locate", "error:locate": "locate",
            "understand_miss": "understand", "error:understand": "understand",
            "hypothesize_miss": "hypothesize", "error:hypothesize": "hypothesize"}


def stage_failures(records, arm=STAGED, subset="clean"):
    """Clean mean share of runs lost at each stage: its miss plus its errors."""
    out = {}
    for o, (m, _, _) in outcome_table(records, arm, subset).items():
        if o in STAGE_OF:
            out[STAGE_OF[o]] = out.get(STAGE_OF[o], 0.0) + m
    return out


def fails_most_stage(records, arm=STAGED):
    """The stage (miss + error) with the largest clean mean share, or 'n/a'."""
    s = stage_failures(records, arm)
    return max(s, key=s.get) if s else "n/a"


def file_level_only_count(records, arm=STAGED, subset="clean"):
    return sum(bool(r.get("file_level_only")) for r in _outcome_rows(records, arm, subset))


def file_level_only_issues(records, arm=STAGED, subset="clean"):
    return len({r["issue"] for r in _outcome_rows(records, arm, subset)
                if r.get("file_level_only")})


def fault_hit_rate(records, arm=STAGED, subset="clean"):
    """Share of fault_hit True among hits that were judged (None excluded), or None."""
    judged = [r["fault_hit"] for r in _outcome_rows(records, arm, subset)
              if r["outcome"] == "hit" and r.get("fault_hit") is not None]
    return sum(judged) / len(judged) if judged else None


def _f(x, spec=".3f"):
    return "n/a" if x is None else format(x, spec)


def markdown(records) -> str:
    arms = [BASELINE, *AGENT_ARMS]
    scored = [RETRIEVAL, *arms, *PIPELINE_ARMS]
    L = []
    L += ["## Scores (mean over repeats; min-max across repeats)", "",
          "| arm | subset | runs | recall@1 | recall@5 | MRR | flip rate |",
          "|---|---|---|---|---|---|---|"]
    for arm in scored:
        for sub in ("all", "clean"):
            s = arm_scores(records, arm, sub)
            if not s:
                L.append(f"| {arm} | {sub} | 0 | no data | | | |")
                continue
            cells = [f"{s[m][0]:.3f} ({s[m][1]:.3f}-{s[m][2]:.3f})" for m in METRICS]
            n = len(_use(records, arm, sub))
            L.append(f"| {arm} | {sub} | {n} | " + " | ".join(cells)
                     + f" | {_f(flip_rate(records, arm, sub))} |")
    for title, pairs in (("Chain: each arm against the one before it", CHAIN),
                         ("Each agent arm against grounded",
                          [(BASELINE, x) for x in (*AGENT_ARMS, *PIPELINE_ARMS)]),
                         ("Staged against agent", [("agent", STAGED)]),
                         ("Each model arm against retrieval",
                          [(RETRIEVAL, x) for x in arms])):
        for sub in ("clean", "all"):
            L += ["", f"## {title} ({sub}, recall@1, b vs a, 95% bootstrap over issues)", "",
                  "| a -> b | issues | wins | losses | ties | diff | 95% interval | verdict |",
                  "|---|---|---|---|---|---|---|---|"]
            for x, y in pairs:
                p = paired(records, x, y, subset=sub)
                if p is None:
                    L.append(f"| {x} -> {y} | 0 | no data | | | | | |")
                    continue
                sig = ("counts" if p["ci_low"] > 0 or p["ci_high"] < 0
                       else "no measurable difference")
                L.append(f"| {x} -> {y} | {p['n']} | {p['wins']} | {p['losses']} | "
                         f"{p['ties']} | {p['diff']:+.3f} | "
                         f"[{p['ci_low']:+.3f}, {p['ci_high']:+.3f}] | {sig} |")
    L += ["", "## Cost and latency", "",
          "| arm | $/triage | $/correct | p50 ms | p95 ms | run errors | api errors |",
          "|---|---|---|---|---|---|---|"]
    for arm in scored:
        if not any(r["arm"] == arm for r in records):
            L.append(f"| {arm} | no data | | | | | |")
            continue
        c = cost_stats(records, arm)
        L.append(f"| {arm} | {_f(c['per_triage'], '.4f')} | {_f(c['per_correct'], '.4f')} | "
                 f"{_f(c['p50_ms'], '.0f')} | {_f(c['p95_ms'], '.0f')} | "
                 f"{c['run_errors']} | {c['api_errors']} |")
    L += ["", "## Tool use", "",
          "| arm | used any | turns mean/p50/max | calls by tool | tool error rate | picks seen |",
          "|---|---|---|---|---|---|"]
    for arm in (*AGENT_ARMS, *PIPELINE_ARMS):
        t = tool_stats(records, arm)
        if not t:
            L.append(f"| {arm} | no data | | | | |")
            continue
        calls = ", ".join(f"{k}={v}" for k, v in sorted(t["calls_by_tool"].items())) or "none"
        L.append(f"| {arm} | {t['used_any']:.2f} | {t['turns_mean']:.1f}/"
                 f"{t['turns_p50']:g}/{t['turns_max']} | {calls} | "
                 f"{_f(t['tool_error_rate'], '.2f')} | {_f(t['picks_seen'], '.2f')} |")
    L += ["", "## Staged outcomes (share of runs; mean over repeats, min-max across repeats)"]
    for sub in ("clean", "all"):
        t = outcome_table(records, STAGED, sub)
        L += ["", f"### {sub}", ""]
        if not t:
            L.append("no data")
            continue
        L += ["| outcome | share |", "|---|---|"]
        L += [f"| {o} | {m:.3f} ({lo:.3f}-{hi:.3f}) |" for o, (m, lo, hi) in t.items()]
    by_stage = ", ".join(f"{k} {v:.3f}" for k, v in stage_failures(records).items())
    L += ["", f"Fails most (clean): {fails_most(records)}",
          f"Fails most by stage, miss + error (clean): {fails_most_stage(records)}"
          + (f" ({by_stage})" if by_stage else ""),
          f"Runs judged at file level only (clean): {file_level_only_count(records)} "
          f"({file_level_only_issues(records)} distinct issues)",
          f"Fault hit rate among judged hits (clean): {_f(fault_hit_rate(records))}"]
    L += ["", f"**Chosen arm: {choose_arm(records)}**"]
    return "\n".join(L)


def main(path) -> None:
    records = load_records(Path(path))
    if not records:
        print(f"no data in {path}")
        return
    keys = [(r["arm"], r["issue"], r["repeat"]) for r in records
            if r.get("error_kind") != "api"]
    dup = len(keys) - len(set(keys))
    if dup:
        print(f"WARNING: {dup} duplicate non-api (arm, issue, repeat) record(s) in {path}; "
              "they are counted twice below")
    print(markdown(records))


if __name__ == "__main__":
    main(sys.argv[1])
