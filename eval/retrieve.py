"""p3b — score retrieval. The number this whole project is built around.

    python -m eval.retrieve --n 40
    python -m eval.retrieve --split val --n 206 --strategy fixed_v1

Definitions, stated explicitly because subtly different ones are not comparable
and you will be quoting these numbers for weeks:

  recall@k   Per issue: of that issue's gold files, what fraction appeared in
             the top k retrieved files? Averaged over issues. NOT hit/miss —
             mean gold count is 1.68, so an issue with two gold files of which
             one is found scores 0.5, not 1.
  MRR        Per issue: 1 / (rank of the FIRST gold file found), 0 if none.
             Averaged. Rewards putting a right answer at the top, where
             recall@10 does not care whether it was rank 1 or rank 10.

  A "hit" is a file, because gold labels are files. A chunk from the right file
  counts, even when the file is 3,685 lines — so this is file-level
  localisation, not line-level. Say so when quoting it.

Scored twice, always: 18.6% of rows name a gold file in the issue text
(`mentions_gold_path`). Finding those is string matching, not retrieval. The
"clean" number excludes them and is the one to quote.
"""
from __future__ import annotations

import argparse
import json
import time

from mining import config as M
from retrieval import search


def query_for(row: dict) -> str:
    """What we hand the retriever.

    Title plus body, as filed. Deliberately not summarised or rewritten by a
    model — Phase 03 measures retrieval, and a model-rewritten query would
    confound it with generation. Phase 04 onwards may change this; if so, record
    it as a separate strategy rather than silently moving the baseline.
    """
    return f"{row['title']}\n\n{row['body']}"


def score_one(row: dict, k_values: list[int], strategy: str) -> dict:
    gold = set(row["gold_files"])
    ranked = search.search_files(query_for(row), strategy=strategy, k=max(k_values))

    out: dict[str, float] = {}
    for k in k_values:
        found = gold & set(ranked[:k])
        out[f"recall@{k}"] = len(found) / len(gold)

    rr = 0.0
    for rank, path in enumerate(ranked, 1):
        if path in gold:
            rr = 1 / rank
            break
    out["mrr"] = rr
    return out


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--split", default="val")
    ap.add_argument("--n", type=int, default=40)
    ap.add_argument("--strategy", default="fixed_v1")
    args = ap.parse_args()

    rows = [json.loads(l) for l in open(M.SPLIT_DIR / f"{args.split}.jsonl")]
    # Fixed slice, lowest issue numbers first, so two runs are comparable.
    slice_ = sorted(rows, key=lambda r: r["issue"])[: args.n]

    k_values = [1, 5, 10]
    totals: dict[str, float] = {}
    clean_totals: dict[str, float] = {}
    clean_n = 0
    started = time.perf_counter()

    for i, row in enumerate(slice_, 1):
        scores = score_one(row, k_values, args.strategy)
        for key, value in scores.items():
            totals[key] = totals.get(key, 0.0) + value
            if not row["mentions_gold_path"]:
                clean_totals[key] = clean_totals.get(key, 0.0) + value
        clean_n += not row["mentions_gold_path"]
        print(f"\r  {i}/{len(slice_)}", end="", flush=True)

    elapsed = time.perf_counter() - started
    n = len(slice_)
    print(f"\r{' ' * 24}\r", end="")

    print(f"  strategy {args.strategy}   {args.split} slice n={n}   {elapsed:.0f}s")
    print("  " + "-" * 56)
    print(f"  {'':<12}{'all':>10}{'clean':>10}   (clean excludes the "
          f"{n - clean_n} rows naming a gold file)")
    for key in [f"recall@{k}" for k in k_values] + ["mrr"]:
        a = totals.get(key, 0.0) / n
        c = clean_totals.get(key, 0.0) / clean_n if clean_n else 0.0
        print(f"  {key:<12}{a:>9.1%}{c:>10.1%}")
    print("  " + "-" * 56)
    print("\n  Record recall@10 (clean) in MEASUREMENTS.md before changing anything.")


if __name__ == "__main__":
    main()
