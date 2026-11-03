"""Summarise patch-run records (data/runs/val-n40-patch.jsonl).

    python -m eval.patch_report [PATH]

Conventions:
- Key = (issue, repeat). The LAST record per key (file order) gives its outcome;
  earlier api/infra records were superseded by a retry or a re-verify.
- Total spend = sum of cost_usd over ALL records (each paid call once; re-verifies are 0.0).
- Keys whose final record has error_kind api/infra/unexpected are excluded from every
  share and rate and reported as counts per kind (`errors`; `api_errors` = the api count).
- cost_per_patch = spend / keys whose final record made a model call (not stale_path,
  not a final error); cost_per_pass = spend / keys with final outcome pass.
- Outcome min/max = range of the share across repeat values.
"""
from __future__ import annotations

import sys
from collections import Counter, defaultdict

ERROR_KINDS = ("api", "infra", "unexpected")


def _div(a, b):
    return a / b if b else None


def summarize(records: list[dict], harness: dict[int, dict]) -> dict:
    final = {}
    for r in records:
        final[(r["issue"], r["repeat"])] = r
    spend = sum(r.get("cost_usd") or 0.0 for r in records)
    errors = Counter(r["error_kind"] for r in final.values() if r.get("error_kind") in ERROR_KINDS)
    used = [r for r in final.values() if r.get("error_kind") not in ERROR_KINDS]

    counts = Counter(r["outcome"] for r in used)
    by_rep = defaultdict(list)
    for r in used:
        by_rep[r["repeat"]].append(r["outcome"])
    outcomes = {}
    for o, n in counts.items():
        shares = [v.count(o) / len(v) for v in by_rep.values()]
        outcomes[o] = {"n": n, "share": n / len(used), "min": min(shares), "max": max(shares)}

    patchable = [r for r in used if r["outcome"] != "stale_path"]
    passes = sum(r["outcome"] == "pass" for r in used)

    def tally(group_key, rows):
        out = {}
        for r in rows:
            d = out.setdefault(group_key(r), {"n": 0, "pass": 0, "stale": 0, "patchable": 0})
            d["n"] += 1
            d["pass"] += r["outcome"] == "pass"
            d["stale"] += r["outcome"] == "stale_path"
            d["patchable"] += r["outcome"] in ("patch_error", "test_fail", "pass")
        for d in out.values():
            d["pass_rate"] = _div(d["pass"], d["patchable"])
        return out

    return {
        "outcomes": outcomes,
        "patchable_pass_rate": _div(passes, len(patchable)),
        "by_source_outcome": tally(lambda r: r["source_outcome"], used),
        "by_fault_hit": tally(lambda r: r.get("source_fault_hit"),
                              [r for r in used if r["source_outcome"] == "hit"]),
        "cost_per_patch": _div(spend, len(patchable)),
        "cost_per_pass": _div(spend, passes),
        "total_spend": spend,
        "harness_invalid": sorted((i, h.get("reason")) for i, h in harness.items()
                                  if h.get("valid") is False),
        "errors": dict(errors),
        "api_errors": errors.get("api", 0),
    }


def _money(x):
    return "n/a" if x is None else f"${x:.3f}"


def _pct(x):
    return "n/a" if x is None else f"{x:.1%}"


def markdown(s: dict) -> str:
    L = ["Outcomes (share over keys with a final outcome; min-max across repeats)",
         f"  {'outcome':<16}{'n':>5}{'share':>9}{'min':>8}{'max':>8}"]
    for o, d in sorted(s["outcomes"].items(), key=lambda kv: -kv[1]["n"]):
        L.append(f"  {o:<16}{d['n']:>5}{_pct(d['share']):>9}{_pct(d['min']):>8}{_pct(d['max']):>8}")
    L += ["", f"patchable pass rate (excl. stale_path): {_pct(s['patchable_pass_rate'])}",
          "", "By source outcome", f"  {'source':<18}{'n':>5}{'stale':>7}{'patchable':>11}{'pass':>6}{'pass/patchable':>16}"]
    for k, d in sorted(s["by_source_outcome"].items(), key=lambda kv: str(kv[0])):
        L.append(f"  {str(k):<18}{d['n']:>5}{d['stale']:>7}{d['patchable']:>11}{d['pass']:>6}{_pct(d['pass_rate']):>16}")
    L += ["", "Among source hits, by fault_hit", f"  {'fault_hit':<18}{'n':>5}{'stale':>7}{'patchable':>11}{'pass':>6}{'pass/patchable':>16}"]
    for k, d in sorted(s["by_fault_hit"].items(), key=lambda kv: str(kv[0])):
        L.append(f"  {str(k):<18}{d['n']:>5}{d['stale']:>7}{d['patchable']:>11}{d['pass']:>6}{_pct(d['pass_rate']):>16}")
    L += ["", f"total spend {_money(s['total_spend'])}   cost/patch {_money(s['cost_per_patch'])}"
              f"   cost/pass {_money(s['cost_per_pass'])}",
          f"errors (excluded): {s['errors'] or 'none'}"]
    if s["harness_invalid"]:
        L += ["", "Harness-invalid issues"] + [f"  #{i}: {r}" for i, r in s["harness_invalid"]]
    return "\n".join(L)


def main(path=None) -> None:
    from .patch_eval import OUT, load_patch_records
    from .verify_harness import load_harness
    p = path or OUT
    records = load_patch_records(p)
    if not records:
        print(f"no data in {p}")
        return
    print(markdown(summarize(records, load_harness())))


if __name__ == "__main__":
    from pathlib import Path
    main(Path(sys.argv[1]) if len(sys.argv) > 1 else None)
