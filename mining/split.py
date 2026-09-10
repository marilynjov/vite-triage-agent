"""Stage 4 — split by time, not at random.

A random split lets the system learn from issues filed *after* the ones it is
tested on, which is not a situation that exists in production. Sorting by
creation date and cutting means train is the past and test is the future,
which is the only honest simulation of "a new issue arrives".
"""
from __future__ import annotations

from . import config as C
from . import jsonl

RATIOS = {"train": 0.70, "val": 0.15, "test": 0.15}


def split() -> None:
    rows = sorted(jsonl.read(C.DATASET_JSONL), key=lambda r: r["created_at"] or "")
    total = len(rows)
    if total < 30:
        raise SystemExit(f"only {total} examples — nothing worth splitting yet")

    n_train = int(total * RATIOS["train"])
    n_val = int(total * RATIOS["val"])
    parts = {
        "train": rows[:n_train],
        "val": rows[n_train:n_train + n_val],
        "test": rows[n_train + n_val:],
    }

    C.SPLIT_DIR.mkdir(parents=True, exist_ok=True)
    for name, part in parts.items():
        path = C.SPLIT_DIR / f"{name}.jsonl"
        jsonl.write(path, part)
        span = f"{part[0]['created_at'][:10]} → {part[-1]['created_at'][:10]}"
        print(f"  {name:<6} {len(part):>5,}   {span}")

    (C.SPLIT_DIR / "DO_NOT_OPEN_TEST.md").write_text(
        "# test.jsonl is sealed until Phase 07\n\n"
        "Every time you look at these examples you spend a little of their\n"
        "value as evidence. Tune on `val`. Open this once, at the end.\n"
    )
    print("\n  val is what you tune against. test stays closed until Phase 07.")
