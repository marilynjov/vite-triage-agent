"""How often the inbox was right in real use, once issues close and the miner labels them.

    python -m mining all        # refresh labels (GitHub reads only, free)
    python -m inbox score

Each issue's latest run without an error is scored against the files its fix
changed, with the same score_ranking every eval uses. Runs on issues the miner
hasn't labelled yet (still open, or closed without a linked fix) are listed as
waiting.
"""
from __future__ import annotations

import json

from eval.retrieve import score_ranking
from mining import config as M

from .triage import RUNS


def score(runs: list[dict], dataset: list[dict]) -> dict:
    latest: dict[int, dict] = {}
    for r in runs:  # file order is time order
        if not r.get("error"):
            latest[r["issue"]] = r
    gold = {row["issue"]: set(row["gold_files"]) for row in dataset}
    scored = [score_ranking(r["files"], gold[i], [1, 5]) for i, r in latest.items() if i in gold]
    n = len(scored)

    def mean(key: str) -> float:
        return sum(s[key] for s in scored) / n if n else 0.0

    return {"n": n, "recall_at_1": mean("recall@1"), "recall_at_5": mean("recall@5"),
            "mrr": mean("mrr"), "waiting": sorted(i for i in latest if i not in gold)}


def _jsonl(path) -> list[dict]:
    return [json.loads(l) for l in path.read_text().splitlines() if l.strip()] if path.exists() else []


def main() -> None:
    s = score(_jsonl(RUNS), _jsonl(M.DATASET_JSONL))
    print("Refresh labels first with `python -m mining all` (free) to score newly closed issues.")
    print(f"scored {s['n']}   recall@1 {s['recall_at_1']:.1%}   recall@5 {s['recall_at_5']:.1%}"
          f"   MRR {s['mrr']:.1%}")
    if s["waiting"]:
        print(f"waiting for a fix: {', '.join(f'#{i}' for i in s['waiting'])}")
