"""The same agent, driven by the SDK's tool runner instead of the hand-written loop.

    PROVIDER=anthropic python -m agent.runner --n 3

Not a production path: it exists to show, on 3 issues, what `tool_runner` does for
you (docs/phase-04-runner.md). The tools are the same four functions from
`agent.tools`, wrapped with `@beta_tool`; `cutoff` is bound by a closure per run so
the model never sees it.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from decimal import Decimal

import anthropic
from anthropic import beta_tool

from api import config as C
from api.schema import GroundedTriageResult
from api.triage import grounded_prompt
from mining import config as M
from retrieval import config as R
from retrieval import search
from eval.retrieve import query_for
from retrieval.search import FileHit

from . import tools
from .loop import AGENT_SYSTEM, OUTPUT_SCHEMA, RunError, _record_turn, run


def _make_tools(cutoff: str) -> list:
    @beta_tool
    def list_dir(path: str = "") -> str:
        """List a directory of the repository. Directories end with '/'.

        Args:
            path: Repo-relative directory; empty for the repository root.
        """
        return tools.list_dir(path)

    @beta_tool
    def read_file(path: str, start_line: int = 1, end_line: int | None = None) -> str:
        """Read a file with line numbers, at most 400 lines per call. The header reports the file's total length.

        Args:
            path: Repo-relative file path.
            start_line: First line, 1-based.
            end_line: Last line; omit for as many as allowed.
        """
        return tools.read_file(path, start_line, end_line)

    @beta_tool
    def grep(pattern: str, path: str = "") -> str:
        """Regex (extended) search; shows up to 50 matches as path:line:text.

        Args:
            pattern: Extended regular expression.
            path: Directory or file to search; empty for everything.
        """
        return tools.grep(pattern, path)

    @beta_tool
    def git_log(path: str = "", n: int = 10) -> str:
        """Recent commits (hash, date, subject, files touched), newest first, up to 30.

        Args:
            path: Limit to commits touching this path; empty for all.
            n: How many commits.
        """
        return tools.git_log(path, n, cutoff=cutoff)

    return [list_dir, read_file, grep, git_log]


def run_with_runner(title: str, body: str, candidates: list[FileHit], cutoff: str,
                    *, client=None, max_iterations: int | None = None):
    """Returns (result or None, the runner's message list, per-turn Turns)."""
    if client is None:
        client = anthropic.Anthropic()
    runner = client.beta.messages.tool_runner(
        model=C.ANTHROPIC_MODEL,
        max_tokens=C.MAX_TOKENS,
        system=AGENT_SYSTEM,
        tools=_make_tools(cutoff),
        output_config={"format": {"type": "json_schema", "schema": OUTPUT_SCHEMA}},
        messages=[{"role": "user", "content": grounded_prompt(title, body, candidates)}],
        max_iterations=max_iterations or C.AGENT_MAX_TURNS,
    )
    last = None
    turns = []
    for message in runner:
        last = message
        turns.append(_record_turn(message, 0))
    # The runner appends the final assistant message to its history only when it
    # continues; on the closing turn it does not, so add it for a complete array.
    messages = list(runner._params["messages"])
    if last is not None:
        messages.append({"role": "assistant", "content": last.content})
    result = None
    if last is not None and last.stop_reason == "end_turn":
        text = next((b.text for b in last.content if b.type == "text"), "")
        try:
            result = GroundedTriageResult.model_validate_json(text)
            tools.check_in_scope(result)
        except ValueError:  # pydantic's ValidationError is a ValueError
            result = None
    return result, messages, turns


def _count_tool_calls(messages: list) -> int:
    n = 0
    for m in messages:
        if m["role"] != "assistant":
            continue
        for b in m["content"]:
            t = b.get("type") if isinstance(b, dict) else b.type
            n += t == "tool_use"
    return n


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--n", type=int, default=3)
    args = ap.parse_args()
    if C.PROVIDER != "anthropic":
        sys.exit("agent.runner needs PROVIDER=anthropic")
    rows = [json.loads(l) for l in open(M.SPLIT_DIR / "val.jsonl")]
    slice_ = sorted(rows, key=lambda r: r["issue"])[: args.n]
    for row in slice_:
        cands = search.search_ranked(query_for(row), strategy=R.DEFAULT_STRATEGY, k=C.GROUNDED_K)
        print(f"\n#{row['issue']}  gold: {row['gold_files']}")
        try:
            r = run(row["title"], row["body"], cands, row["created_at"])
            print(f"  loop    files={r.result.suspected_files}\n          turns={len(r.turns)} "
                  f"tools={len(r.trace)} in={r.input_tokens} out={r.output_tokens} ${r.cost_usd:.4f}")
        except RunError as exc:
            r = exc.run
            print(f"  loop    FAILED {exc}\n          turns={len(r.turns)} ${r.cost_usd:.4f}")
        result, msgs, turns = run_with_runner(row["title"], row["body"], cands, row["created_at"])
        cost = sum((t.cost_usd for t in turns), Decimal("0"))
        files = result.suspected_files if result else None
        print(f"  runner  files={files}\n          turns={len(turns)} tools={_count_tool_calls(msgs)} "
              f"in={sum(t.input_tokens for t in turns)} out={sum(t.output_tokens for t in turns)} ${cost:.4f}")


if __name__ == "__main__":
    main()
