"""p2e — score classification against the GitHub labels the miner captured.

    python -m eval.classify              # 40-row slice, current PROVIDER
    PROVIDER=anthropic python -m eval.classify --n 40

Ground truth comes free from labels, with two caveats this script prints rather
than hides:

  severity   derived from Vite's priority labels (p2->low, p3->medium,
             p4->high). 62% of val rows carry one. The majority class is
             `medium` at ~60%, so ALWAYS compare accuracy to that baseline and
             look at the prediction spread: a model that answers `medium` every
             time scores 60% and knows nothing.

  component  derived from `feat: *` labels; only ~29% of rows carry one, so this
             number rests on fewer rows and is noisier per-row, but it is the
             axis actually present in the issue text.

The slice is fixed (lowest issue numbers first), never random, so two runs are
comparable.
"""
from __future__ import annotations

import argparse
import json
import re
import time
from collections import Counter

from pydantic import ValidationError

from api import config as C, cost
from api import triage
from mining import config as M  # splits live with the miner, not the api

PRIORITY = re.compile(r"^p([1-5])-")
TIER = {1: "low", 2: "low", 3: "medium", 4: "high", 5: "high"}


def truth(row: dict) -> tuple[str | None, list[str]]:
    hits = [int(m.group(1)) for l in row["labels"] if (m := PRIORITY.match(l))]
    severity = TIER[min(hits)] if hits else None
    components = [l.removeprefix("feat: ") for l in row["labels"] if l.startswith("feat:")]
    return severity, components


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--n", type=int, default=40)
    ap.add_argument("--split", default="val")
    args = ap.parse_args()

    rows = [json.loads(l) for l in open(M.SPLIT_DIR / f"{args.split}.jsonl")]
    # Fixed slice: scoreable on severity, lowest issue numbers first.
    slice_ = sorted(
        (r for r in rows if truth(r)[0]), key=lambda r: r["issue"]
    )[: args.n]

    sev_hit = sev_n = comp_hit = comp_n = invalid = 0
    invalid_reasons: Counter[str] = Counter()
    preds: Counter[str] = Counter()
    confusion: Counter[tuple[str, str]] = Counter()
    spend = 0
    started = time.perf_counter()

    for i, row in enumerate(slice_, 1):
        true_sev, true_comps = truth(row)
        try:
            completion = triage.triage_structured(row["title"], row["body"])
        except (ValidationError, ValueError) as exc:
            # The model produced something the contract rejects. Constrained
            # decoding guarantees shape but not value constraints (maxItems,
            # maxLength, numeric ranges), so this is expected at some rate and
            # that rate is worth reporting.
            invalid += 1
            invalid_reasons[type(exc).__name__] += 1
            print(f"\r  {i}/{len(slice_)}  invalid: {invalid}", end="", flush=True)
            continue
        p = completion.parsed
        spend += cost.compute_cost(completion)

        preds[p.severity] += 1
        sev_n += 1
        sev_hit += p.severity == true_sev
        confusion[(true_sev, p.severity)] += 1

        if true_comps:
            comp_n += 1
            blob = " ".join(p.suspected_components).lower()
            comp_hit += any(t.lower() in blob for t in true_comps)

        print(f"\r  {i}/{len(slice_)}", end="", flush=True)

    elapsed = time.perf_counter() - started
    print(f"\r{' ' * 20}\r", end="")

    print(f"  {C.PROVIDER} / {C.active_model()}   {len(slice_)} rows   "
          f"{elapsed:.0f}s   ${spend:.4f}")
    print("  " + "-" * 54)
    base = max(Counter(truth(r)[0] for r in slice_).values()) / len(slice_)
    print(f"  contract violations  {invalid}/{len(slice_)}  "
          f"{invalid/len(slice_):.0%}   {dict(invalid_reasons) or ''}")
    print(f"  severity    {sev_hit}/{sev_n}  {sev_hit/sev_n:>5.0%}"
          f"   (majority-class baseline {base:.0%})")
    if comp_n:
        print(f"  component   {comp_hit}/{comp_n}  {comp_hit/comp_n:>5.0%}"
              f"   (on the {comp_n} rows with a feat: label)")
    print(f"\n  prediction spread  {dict(preds)}")
    if len(preds) == 1:
        print("  !! one class only — this model has no discriminative power here")
    print("\n  confusion (truth -> predicted)")
    for (t, p_), n in sorted(confusion.items()):
        flag = "" if t == p_ else "  <-- miss"
        print(f"    {t:<7} -> {p_:<7} {n:>3}{flag}")


if __name__ == "__main__":
    main()
