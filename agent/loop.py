"""The tool-use loop, written out by hand against `client.messages.create`.

Read it top to bottom and you have the whole conversation:

    1. send the issue, the tools, and the output schema
    2. the model answers with `stop_reason`:
         tool_use  -> run every tool it asked for, send ALL the results back in
                      ONE user message, go to 2
         end_turn  -> the text block is the final JSON; validate it, done
         otherwise -> error
    3. the turn limit stops a model that never finishes

Two rules the API enforces and this loop obeys:

  * History is append-only. Haiku 5.5 thinks by default, and a resent thinking
    block is only valid if everything before it is unchanged. So the assistant
    message is `response.content` appended whole, and nothing is ever edited.
  * Every `tool_use` block needs a `tool_result` with the matching id, and the
    results for one response all travel in a single user message.
"""
from __future__ import annotations

import time
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any

import anthropic
from pydantic import ValidationError

from api import config as C
from api import cost
from api.providers.base import Completion
from api.schema import GroundedTriageResult, ToolCall
from api.triage import GROUNDED_SYSTEM, grounded_prompt
from retrieval.search import FileHit

from . import tools

# The SDK's transform strips constraints the API rejects (maxItems, maxLength...).
# Constrained decoding is not validation: Pydantic still enforces them on the final text.
OUTPUT_SCHEMA = anthropic.transform_schema(GroundedTriageResult)

AGENT_SYSTEM = GROUNDED_SYSTEM + """
**Tools**

The candidate files are a starting point, not a boundary. You have read-only
tools over the repository at the commit the candidates were indexed from; use
them to check a candidate before you rank it, and to look for the file the fix
really lives in when the candidates do not show it.

- `grep(pattern, path)`: search the code for a regular expression. Best for
  finding where a symbol, option or error message is defined or used.
- `read_file(path, start_line, end_line)`: read numbered lines of a file.
  It returns at most 400 lines per call; ask for a range to read further.
- `list_dir(path)`: list a directory, to find sibling files.
- `git_log(path, n)`: recent history of a file, for context on how it changes.

A file outside the candidate list may go in `suspected_files` once a tool has
confirmed it exists and matches the report. When you have enough evidence,
stop calling tools and give the final answer.
"""


@dataclass
class Turn:
    """One API call: what it used, why it stopped, what it cost."""
    input_tokens: int
    output_tokens: int
    cache_creation_input_tokens: int
    cache_read_input_tokens: int
    stop_reason: str | None
    request_id: str | None
    latency_ms: int
    cost_usd: Decimal


@dataclass
class Run:
    result: GroundedTriageResult | None = None
    messages: list = field(default_factory=list)
    turns: list[Turn] = field(default_factory=list)
    trace: list[ToolCall] = field(default_factory=list)

    @property
    def input_tokens(self) -> int:
        return sum(t.input_tokens for t in self.turns)

    @property
    def output_tokens(self) -> int:
        return sum(t.output_tokens for t in self.turns)

    @property
    def cost_usd(self) -> Decimal:
        return sum((t.cost_usd for t in self.turns), Decimal("0"))


class RunError(Exception):
    """The run did not end in a valid answer. `.run` holds the turns already paid for."""

    def __init__(self, message: str, run: Run):
        super().__init__(message)
        self.run = run


def _record_turn(response: Any, latency_ms: int) -> Turn:
    usage = response.usage
    completion = Completion(
        text="", model=C.ANTHROPIC_MODEL, provider="anthropic",
        input_tokens=getattr(usage, "input_tokens", 0) or 0,
        output_tokens=getattr(usage, "output_tokens", 0) or 0,
        cache_creation_input_tokens=getattr(usage, "cache_creation_input_tokens", 0) or 0,
        cache_read_input_tokens=getattr(usage, "cache_read_input_tokens", 0) or 0,
        stop_reason=response.stop_reason,
        request_id=getattr(response, "_request_id", None),
    )
    return Turn(
        input_tokens=completion.input_tokens,
        output_tokens=completion.output_tokens,
        cache_creation_input_tokens=completion.cache_creation_input_tokens,
        cache_read_input_tokens=completion.cache_read_input_tokens,
        stop_reason=completion.stop_reason,
        request_id=completion.request_id,
        latency_ms=latency_ms,
        cost_usd=cost.compute_cost(completion),
    )


def run(
    title: str,
    body: str,
    candidates: list[FileHit],
    cutoff: str,
    *,
    max_turns: int | None = None,
    client: Any = None,
) -> Run:
    if client is None:
        # built here, not at import, so tests never need a key
        client = anthropic.Anthropic()
    max_turns = max_turns or C.AGENT_MAX_TURNS

    out = Run()
    out.messages.append({"role": "user", "content": grounded_prompt(title, body, candidates)})

    for turn in range(1, max_turns + 1):
        started = time.perf_counter()
        response = client.messages.create(
            model=C.ANTHROPIC_MODEL,
            max_tokens=C.MAX_TOKENS,
            system=AGENT_SYSTEM,
            tools=tools.TOOL_SPECS,
            output_config={"format": {"type": "json_schema",
                                      "schema": OUTPUT_SCHEMA}},
            messages=out.messages,
        )
        out.turns.append(_record_turn(response, int((time.perf_counter() - started) * 1000)))

        if response.stop_reason == "tool_use":
            # The whole content, thinking blocks included, exactly as received.
            out.messages.append({"role": "assistant", "content": response.content})
            results = []
            for block in response.content:
                if block.type != "tool_use":
                    continue
                t0 = time.perf_counter()
                output, is_error = tools.execute(block.name, block.input, cutoff=cutoff)
                out.trace.append(ToolCall(
                    turn=turn, name=block.name, input=dict(block.input),
                    output=output[:2000], is_error=is_error,
                    duration_ms=int((time.perf_counter() - t0) * 1000),
                ))
                results.append({"type": "tool_result", "tool_use_id": block.id,
                                "content": output, "is_error": is_error})
            out.messages.append({"role": "user", "content": results})
            continue

        if response.stop_reason == "end_turn":
            # The closing turn belongs in the history too, even if it is rejected below.
            out.messages.append({"role": "assistant", "content": response.content})
            text = next((b.text for b in response.content if b.type == "text"), "")
            try:
                result = GroundedTriageResult.model_validate_json(text)
                tools.check_in_scope(result)
            except (ValidationError, ValueError) as exc:
                raise RunError(f"final answer rejected: {exc}", out) from exc
            out.result = result
            return out

        raise RunError(f"unexpected stop_reason {response.stop_reason!r} on turn {turn}", out)

    raise RunError(f"no final answer after {max_turns} turns", out)
