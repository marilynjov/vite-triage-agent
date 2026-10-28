"""Print a tool-use run's message array turn by turn.

    PROVIDER=anthropic python -m agent.explain 13907 [--json PATH]

Who appends what: the caller appends the first user message; after each API call
the loop appends the assistant message (the model's blocks, verbatim); if that
message holds tool_use blocks, the loop appends ONE user message with a
tool_result per tool_use, matched by id. Drop one and the next call is rejected.
"""
from __future__ import annotations

import argparse
import json
import sys
from typing import Any

from agent.loop import Run, RunError, Turn, run
from api import config as C
from mining import config as M
from retrieval import config as R
from retrieval import search

from eval.retrieve import query_for


def _field(block: Any, name: str, default: Any = None) -> Any:
    if isinstance(block, dict):
        return block.get(name, default)
    return getattr(block, name, default)


def _clip(text: Any, n: int = 70) -> str:
    s = " ".join(str(text).split())
    return s if len(s) <= n else s[: n - 1] + "…"


LEGEND = """HOW TO READ THIS
  [0]            the caller's first user message (a plain string).
  assistant [k]  response.content appended whole by the loop after API call k.
  user [k+1]     the loop's tool results: one tool_result per tool_use, matched
                 by id, all in ONE user message.
  A missing tool_result for any tool_use id makes the next API call fail.
"""

FOOTER = "tool_result text clipped to 60 chars; --json has it in full"


def _short_id(tid: Any) -> str:
    tid = str(tid)
    return tid if len(tid) <= 14 else tid[:13] + "…"


def _block_line(block: Any, uses: dict) -> str:
    kind = _field(block, "type")
    if kind == "tool_use":
        return (f"tool_use {_field(block, 'id')} {_field(block, 'name')}  "
                f"input={_clip(json.dumps(_field(block, 'input', {})), 60)}")
    if kind == "tool_result":
        tid = _field(block, "tool_use_id")
        idx, name = uses.get(tid, ("?", "?"))
        return (f"tool_result {_short_id(tid)} {name}  answers [{idx}]  "
                f"is_error={_field(block, 'is_error', False)}  "
                f'"{_clip(_field(block, "content", ""), 60)}"')
    if kind == "thinking":
        text = _field(block, "thinking", "")
        if text:
            return f"thinking  {_clip(text)}"
        sig = _field(block, "signature")
        extra = f", signature {len(sig)} chars" if sig else ""
        return f"thinking  (text hidden by the API; resent unchanged{extra})"
    if kind == "text":
        return f"text  {_clip(_field(block, 'text', ''))}"
    return str(kind)


def format_transcript(messages: list, turns: list[Turn]) -> str:
    uses: dict[str, tuple[int, str]] = {}
    for i, msg in enumerate(messages):
        if msg["role"] == "assistant" and not isinstance(msg["content"], str):
            for blk in msg["content"]:
                if _field(blk, "type") == "tool_use":
                    uses[_field(blk, "id")] = (i, _field(blk, "name"))
    lines: list[str] = [LEGEND]
    api_call = 0
    for i, msg in enumerate(messages):
        role = msg["role"]
        content = msg["content"]
        lines.append(f"[{i}] {role}")
        if isinstance(content, str):
            lines.append(f"      text  {_clip(content, 90)}")
            continue
        for block in content:
            lines.append(f"      {_block_line(block, uses)}")
        if role == "assistant":
            if api_call < len(turns):
                t = turns[api_call]
                lines.append(
                    f"      -- API call {api_call + 1}: input={t.input_tokens} "
                    f"output={t.output_tokens} cache_write={t.cache_creation_input_tokens} "
                    f"cache_read={t.cache_read_input_tokens} "
                    f"stop_reason={t.stop_reason} cost=${t.cost_usd}")
            api_call += 1
    lines.append("")
    lines.append(FOOTER)
    return "\n".join(lines)


def _jsonable(obj: Any) -> Any:
    if isinstance(obj, dict):
        return {k: _jsonable(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [_jsonable(v) for v in obj]
    if hasattr(obj, "model_dump"):
        return obj.model_dump()
    return obj


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("issue", type=int)
    ap.add_argument("--json", dest="json_path")
    args = ap.parse_args()
    if C.PROVIDER != "anthropic":
        sys.exit("agent.explain needs PROVIDER=anthropic")

    rows = [json.loads(l) for l in open(M.SPLIT_DIR / "val.jsonl")]
    row = next((r for r in rows if r["issue"] == args.issue), None)
    if row is None:
        sys.exit(f"issue {args.issue} not in val")
    candidates = search.search_ranked(
        query_for(row), strategy=R.DEFAULT_STRATEGY, k=C.GROUNDED_K)

    error = None
    try:
        result = run(row["title"], row["body"], candidates, row["created_at"])
        the_run: Run = result
        picked = result.result.suspected_files
    except RunError as exc:
        the_run, picked, error = exc.run, [], exc

    print(f"issue #{row['issue']}: {row['title']}")
    print(f"candidates: {len(candidates)}  turns: {len(the_run.turns)}  "
          f"tool calls: {len(the_run.trace)}  cost: ${the_run.cost_usd}\n")
    print(format_transcript(the_run.messages, the_run.turns))
    print()
    if error:
        print(f"RUN FAILED: {error}")
    gold = set(row["gold_files"])
    print("gold files:      " + ", ".join(sorted(gold)))
    print("suspected files: " + ", ".join(picked))
    hits = [p for p in picked if p in gold]
    print("hits:            " + (", ".join(hits) if hits else "none"))

    if args.json_path:
        with open(args.json_path, "w") as f:
            json.dump(_jsonable(the_run.messages), f, indent=2, default=str)
        print(f"\nraw array written to {args.json_path}")


if __name__ == "__main__":
    main()
