"""The failure taxonomy: 20 agent misses, read and labelled by hand.

    python -m eval.taxonomy sample    # writes data/taxonomy-sample.md to read from
    python -m eval.taxonomy report    # counts per category from the labels

The sample is fixed: the clean failures (recall@1 = 0, not an api error) of the
`agent` arm on all 206 val rows, drawn with seed 0 and sorted by issue. The labels
are committed in docs/failure-taxonomy.jsonl, one {issue, repeat, category, note}
per line, and the categories are defined in docs/failure-taxonomy.md. Reads only
the val split.
"""
from __future__ import annotations

import json
import random
import sys
from collections import Counter
from pathlib import Path

from mining import config as M

from .ablation import load_records, val_rows

SOURCE = M.DATA / "runs" / "val-n206-r1.jsonl"
LABELS = M.ROOT / "docs" / "failure-taxonomy.jsonl"
SAMPLE_MD = M.DATA / "taxonomy-sample.md"
ARM = "agent"
BODY_CHARS = 1500


def failures(records: list[dict]) -> list[dict]:
    return [r for r in records
            if r["arm"] == ARM and r.get("error_kind") != "api"
            and not r["mentions_gold_path"] and r["recall_at_1"] == 0]


def sample_failures(records: list[dict], n: int = 20, seed: int = 0) -> list[dict]:
    eligible = sorted(failures(records), key=lambda r: (r["issue"], r["repeat"]))
    if len(eligible) < n:
        raise ValueError(f"only {len(eligible)} failures to sample {n} from")
    return sorted(random.Random(seed).sample(eligible, n), key=lambda r: (r["issue"], r["repeat"]))


def load_labels(path: Path = LABELS) -> list[dict]:
    if not Path(path).exists():
        return []
    labels, seen = [], set()
    for i, line in enumerate(Path(path).read_text().splitlines(), 1):
        if not line.strip():
            continue
        lab = json.loads(line)
        if not str(lab.get("category") or "").strip():
            raise ValueError(f"{path}:{i}: no category")
        key = (lab["issue"], lab["repeat"])
        if key in seen:
            raise ValueError(f"{path}:{i}: duplicate label for {key}")
        seen.add(key)
        labels.append(lab)
    return labels


def counts(labels: list[dict]) -> list[tuple[str, int]]:
    return sorted(Counter(l["category"] for l in labels).items(), key=lambda kv: (-kv[1], kv[0]))


def _tool_line(t: dict) -> str:
    arg = json.dumps(t.get("input"))[:160]
    return f"- `{t['name']}` {arg}" + (" **error**" if t.get("is_error") else "")


def render(sample: list[dict], rows: dict[int, dict]) -> str:
    out = [f"# Failure sample: {len(sample)} clean `{ARM}` misses from `{SOURCE.name}`\n"]
    for r in sample:
        row = rows.get(r["issue"], {})
        body = (row.get("body") or "")[:BODY_CHARS]
        out += [f"## #{r['issue']} r{r['repeat']}: {row.get('title', '')}\n",
                f"- gold: {', '.join(f'`{g}`' for g in r['gold'])}",
                f"- picked: {', '.join(f'`{p}`' for p in r['picked'][:5]) or '(none)'}",
                f"- candidates: {', '.join(f'`{c}`' for c in r['candidates'])}",
                f"- gold in candidates at: {[r['candidates'].index(g) + 1 for g in r['gold'] if g in r['candidates']]}",
                f"- turns {r['turns']}, tool calls {len(r['tool_calls'] or [])}, error: {r.get('error')}",
                *[_tool_line(t) for t in r["tool_calls"] or []],
                "", "```text", body, "```", ""]
    return "\n".join(out)


def main(argv: list[str]) -> None:
    cmd = argv[0] if argv else "report"
    if cmd == "sample":
        sample = sample_failures(load_records(SOURCE))
        rows = {r["issue"]: r for r in val_rows("val", 10_000)}
        SAMPLE_MD.write_text(render(sample, rows))
        print(f"wrote {SAMPLE_MD} ({len(sample)} failures)")
    elif cmd == "report":
        labels = load_labels()
        print(f"{len(labels)} labelled failures")
        for cat, n in counts(labels):
            print(f"  {cat:<28}{n:>3}")
    else:
        raise SystemExit("usage: python -m eval.taxonomy [sample|report]")


if __name__ == "__main__":
    main(sys.argv[1:])
