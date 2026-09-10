"""Stage 5 — read your own data. The task everyone skips and shouldn't.

Ten examples read by hand will tell you more about whether this project is
viable than any aggregate the report can print.
"""
from __future__ import annotations

import random
import textwrap

from . import config as C
from . import jsonl


def inspect(n: int = 10, seed: int = 0) -> None:
    rows = list(jsonl.read(C.DATASET_JSONL))
    random.Random(seed).shuffle(rows)

    for i, row in enumerate(rows[:n], 1):
        flag = "  [names a gold file]" if row["mentions_gold_path"] else ""
        print(f"\n{'=' * 70}")
        print(f"{i}. #{row['issue']}  {row['title']}{flag}")
        print(f"   https://github.com/{C.OWNER}/{C.NAME}/issues/{row['issue']}")
        print(f"{'-' * 70}")
        body = " ".join(row["body"].split())[:600]
        print(textwrap.fill(body, 68, initial_indent="   ", subsequent_indent="   "))
        print(f"\n   GOLD ({row['n_gold']}):")
        for path in row["gold_files"]:
            print(f"     {path}")

    print(f"\n{'=' * 70}")
    print("Ask of each one: could a competent engineer who knows this codebase")
    print("find those files from that text alone? If no, the label is noise.")
