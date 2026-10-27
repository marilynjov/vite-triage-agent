"""Does a model re-ranking retrieval's candidates beat retrieval alone?

    python -m eval.grounded                      # 40-row val slice, current PROVIDER
    PROVIDER=anthropic python -m eval.grounded --n 40

Both rankings are scored on the same rows, from the same candidate list, by
the same score_ranking — so the only variable is who ordered the files.

What to read, decided before the first run:

  recall@1, MRR  the model picks only from retrieval's top 10, so it cannot
                 find a file retrieval missed. Rank is what it can change.
  ceiling        retrieval's own recall@10: the best the model could do.
  recall@5       the model names at most 5 files, so this is where an
                 over-cautious model (returns one file, or none) shows up.

A row whose output breaks the contract — an invented path, a maxItems breach —
scores zero for the model: in production that request is a 500. The count is
printed, because a model that is right when valid and often invalid is not
right.
"""
from __future__ import annotations

import argparse
import json
import time
from collections import Counter

from pydantic import ValidationError

from api import config as C, cost
from api import triage
from mining import config as M
from retrieval import config as R
from retrieval import search

from .retrieve import query_for, score_ranking

K_VALUES = [1, 5]


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--split", default="val")
    ap.add_argument("--n", type=int, default=40)
    ap.add_argument("--strategy", default=R.DEFAULT_STRATEGY)
    args = ap.parse_args()

    rows = [json.loads(l) for l in open(M.SPLIT_DIR / f"{args.split}.jsonl")]
    # Fixed slice, lowest issue numbers first, as in eval.retrieve.
    slice_ = sorted(rows, key=lambda r: r["issue"])[: args.n]

    # totals[ranker][subset][metric]
    totals = {r: {"all": Counter(), "clean": Counter()} for r in ("retrieval", "model")}
    ceiling = {"all": 0.0, "clean": 0.0}
    clean_n = invalid = 0
    invalid_reasons: Counter[str] = Counter()
    n_files: Counter[int] = Counter()
    spend = 0
    started = time.perf_counter()

    for i, row in enumerate(slice_, 1):
        gold = set(row["gold_files"])
        candidates = search.search_ranked(
            query_for(row), strategy=args.strategy, k=C.GROUNDED_K
        )
        retrieved = [f.path for f in candidates]

        try:
            completion = triage.triage_grounded(row["title"], row["body"], candidates)
            picked = completion.parsed.suspected_files
            spend += cost.compute_cost(completion)
        except (ValidationError, ValueError) as exc:
            invalid += 1
            invalid_reasons[type(exc).__name__] += 1
            picked = []
        n_files[len(picked)] += 1

        subsets = ["all"] if row["mentions_gold_path"] else ["all", "clean"]
        clean_n += "clean" in subsets
        cap = score_ranking(retrieved, gold, [C.GROUNDED_K])[f"recall@{C.GROUNDED_K}"]
        for ranker, ranked in (("retrieval", retrieved), ("model", picked)):
            for key, value in score_ranking(ranked, gold, K_VALUES).items():
                for s in subsets:
                    totals[ranker][s][key] += value
        for s in subsets:
            ceiling[s] += cap
        print(f"\r  {i}/{len(slice_)}  invalid: {invalid}", end="", flush=True)

    elapsed = time.perf_counter() - started
    n = len(slice_)
    print(f"\r{' ' * 32}\r", end="")

    print(f"  {C.PROVIDER} / {C.active_model()}   strategy {args.strategy}   "
          f"{args.split} n={n}   {elapsed:.0f}s   ${spend:.4f}")
    print("  " + "-" * 62)
    print(f"  {'':<12}{'retrieval':>11}{'model':>9}   {'retrieval':>11}{'model':>9}")
    print(f"  {'':<12}{'all':>20}   {'clean (n=' + str(clean_n) + ')':>20}")
    for key in [f"recall@{k}" for k in K_VALUES] + ["mrr"]:
        cells = []
        for s, d in (("all", n), ("clean", clean_n)):
            for ranker in ("retrieval", "model"):
                cells.append(totals[ranker][s][key] / d if d else 0.0)
        print(f"  {key:<12}{cells[0]:>11.1%}{cells[1]:>9.1%}   {cells[2]:>11.1%}{cells[3]:>9.1%}")
    c_all = ceiling["all"] / n
    c_clean = ceiling["clean"] / clean_n if clean_n else 0.0
    print(f"  {'ceiling':<12}{'':>11}{c_all:>9.1%}   {'':>11}{c_clean:>9.1%}"
          f"   (retrieval recall@{C.GROUNDED_K})")
    print("  " + "-" * 62)
    print(f"  contract violations  {invalid}/{n}  {dict(invalid_reasons) or ''}")
    print(f"  files named per row  {dict(sorted(n_files.items()))}")


if __name__ == "__main__":
    main()
