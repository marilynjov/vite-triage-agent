"""Does an agent with read-only code tools beat re-ranking retrieval's candidates?

    PROVIDER=anthropic python -m eval.agent --n 40

Three rankings on the same rows, all scored by score_ranking:

  retrieval  the candidate order
  grounded   one model call re-ranks the top 10 (cannot leave the list)
  agent      the tool loop; may name a file outside the list once a tool
             confirmed it

What to read: recall@1/MRR against grounded; "gold found outside candidates",
the files only the agent could have found; contract violations for both (a
failed run scores zero, as in production it is a 500); turns and tool calls,
which are the cost drivers.
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from collections import Counter
from decimal import Decimal

from pydantic import ValidationError

from agent.loop import RunError, run
from api import config as C, cost
from api import triage
from mining import config as M
from retrieval import config as R
from retrieval import search

from .retrieve import query_for, score_ranking

K_VALUES = [1, 5]
RANKERS = ("retrieval", "grounded", "agent")


def found_outside(picked: list[str], candidates: list[str], gold: set[str]) -> set[str]:
    """Gold files the agent named that retrieval's candidates lacked."""
    return (set(picked) & gold) - set(candidates)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--split", default="val")
    ap.add_argument("--n", type=int, default=40)
    ap.add_argument("--strategy", default=R.DEFAULT_STRATEGY)
    args = ap.parse_args()
    if C.PROVIDER != "anthropic":
        sys.exit("eval.agent needs PROVIDER=anthropic (the agent uses tool use)")

    rows = [json.loads(l) for l in open(M.SPLIT_DIR / f"{args.split}.jsonl")]
    slice_ = sorted(rows, key=lambda r: r["issue"])[: args.n]

    totals = {r: {"all": Counter(), "clean": Counter()} for r in RANKERS}
    ceiling = {"all": 0.0, "clean": 0.0}
    clean_n = 0
    invalid = {"grounded": 0, "agent": 0}
    reasons = {"grounded": Counter(), "agent": Counter()}
    turns_dist: Counter[int] = Counter()
    tool_calls: Counter[str] = Counter()
    tool_errors = 0
    outside_rows = 0
    outside_files = 0
    outside_paths: list[tuple[int, set[str]]] = []
    spend = {"grounded": Decimal("0"), "agent": Decimal("0")}
    started = time.perf_counter()

    for i, row in enumerate(slice_, 1):
        gold = set(row["gold_files"])
        candidates = search.search_ranked(
            query_for(row), strategy=args.strategy, k=C.GROUNDED_K
        )
        retrieved = [f.path for f in candidates]

        try:
            completion = triage.triage_grounded(row["title"], row["body"], candidates)
            grounded = completion.parsed.suspected_files
            spend["grounded"] += cost.compute_cost(completion)
        except (ValidationError, ValueError) as exc:
            invalid["grounded"] += 1
            reasons["grounded"][type(exc).__name__] += 1
            grounded = []

        try:
            result = run(row["title"], row["body"], candidates, row["created_at"])
            agent_run = result
            agent = result.result.suspected_files
        except RunError as exc:
            agent_run = exc.run
            invalid["agent"] += 1
            reasons["agent"][type(exc).__name__ + ": " + str(exc).split(":")[0][:40]] += 1
            agent = []
        spend["agent"] += agent_run.cost_usd
        turns_dist[len(agent_run.turns)] += 1
        for t in agent_run.trace:
            tool_calls[t.name] += 1
            tool_errors += t.is_error

        out = found_outside(agent, retrieved, gold)
        if out:
            outside_rows += 1
            outside_files += len(out)
            outside_paths.append((row["issue"], out))

        subsets = ["all"] if row["mentions_gold_path"] else ["all", "clean"]
        clean_n += "clean" in subsets
        cap = score_ranking(retrieved, gold, [C.GROUNDED_K])[f"recall@{C.GROUNDED_K}"]
        for ranker, ranked in (("retrieval", retrieved), ("grounded", grounded), ("agent", agent)):
            for key, value in score_ranking(ranked, gold, K_VALUES).items():
                for s in subsets:
                    totals[ranker][s][key] += value
        for s in subsets:
            ceiling[s] += cap
        print(f"\r  {i}/{len(slice_)}  invalid g/a: {invalid['grounded']}/{invalid['agent']}",
              end="", flush=True)

    elapsed = time.perf_counter() - started
    n = len(slice_)
    print(f"\r{' ' * 40}\r", end="")

    total = spend["grounded"] + spend["agent"]
    print(f"  {C.PROVIDER} / {C.active_model()}   strategy {args.strategy}   "
          f"{args.split} n={n}   {elapsed:.0f}s   ${total:.4f}")
    print("  " + "-" * 74)
    print(f"  {'':<10}{'retrieval':>10}{'grounded':>10}{'agent':>8}   "
          f"{'retrieval':>10}{'grounded':>10}{'agent':>8}")
    print(f"  {'':<10}{'all':>28}   {'clean (n=' + str(clean_n) + ')':>28}")
    for key in [f"recall@{k}" for k in K_VALUES] + ["mrr"]:
        cells = []
        for s, d in (("all", n), ("clean", clean_n)):
            for ranker in RANKERS:
                cells.append(totals[ranker][s][key] / d if d else 0.0)
        print(f"  {key:<10}{cells[0]:>10.1%}{cells[1]:>10.1%}{cells[2]:>8.1%}   "
              f"{cells[3]:>10.1%}{cells[4]:>10.1%}{cells[5]:>8.1%}")
    c_all = ceiling["all"] / n
    c_clean = ceiling["clean"] / clean_n if clean_n else 0.0
    print(f"  {'ceiling':<10}{c_all:>28.1%}   {c_clean:>28.1%}"
          f"   (retrieval recall@{C.GROUNDED_K})")
    print("  " + "-" * 74)
    print(f"  gold found outside candidates: {outside_rows} rows, {outside_files} files")
    for issue, files in outside_paths:
        print(f"    #{issue}: {sorted(files)}")
    print(f"  contract violations  grounded {invalid['grounded']}/{n} "
          f"{dict(reasons['grounded']) or ''}   agent {invalid['agent']}/{n} "
          f"{dict(reasons['agent']) or ''}")
    print(f"  turns per run        {dict(sorted(turns_dist.items()))}")
    print(f"  tool calls           {dict(tool_calls.most_common())}  "
          f"(errors: {tool_errors})")
    print(f"  cost                 grounded ${spend['grounded']:.4f} "
          f"(${spend['grounded'] / n:.4f} each)   agent ${spend['agent']:.4f} "
          f"(${spend['agent'] / n:.4f} each)")


if __name__ == "__main__":
    main()
