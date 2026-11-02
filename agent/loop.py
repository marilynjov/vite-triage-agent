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

import logging
import time
from dataclasses import dataclass, field
from decimal import Decimal
import os
from typing import Any, Callable

import anthropic
from pydantic import BaseModel, ValidationError

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
_SCHEMAS: dict[type[BaseModel], dict] = {GroundedTriageResult: OUTPUT_SCHEMA}


def _schema_for(model: type[BaseModel]) -> dict:
    """transform_schema once per output model."""
    if model not in _SCHEMAS:
        _SCHEMAS[model] = anthropic.transform_schema(model)
    return _SCHEMAS[model]

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
    result: BaseModel | None = None
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


class BudgetExceeded(RunError):
    """A configured limit stopped the run. `.limit` names it, `.value` is its setting."""

    def __init__(self, message: str, run: Run, *, limit: str, value: float):
        super().__init__(message, run)
        self.limit = limit
        self.value = value


@dataclass(frozen=True)
class Budget:
    max_turns: int
    max_tokens: int
    max_seconds: float
    max_dollars: float

    @classmethod
    def from_config(cls) -> "Budget":
        # read the env here so a changed value is picked up without a reload
        def get(name, cast, default):
            raw = os.getenv(name)
            return cast(raw) if raw not in (None, "") else default
        return cls(
            max_turns=get("AGENT_MAX_TURNS", int, C.AGENT_MAX_TURNS),
            max_tokens=get("AGENT_MAX_TOKENS", int, C.AGENT_MAX_TOKENS),
            max_seconds=get("AGENT_MAX_SECONDS", float, C.AGENT_MAX_SECONDS),
            max_dollars=get("AGENT_MAX_DOLLARS", float, C.AGENT_MAX_DOLLARS),
        )


# module-level so tests can patch the clock
_now = time.monotonic


def _emit(on_event: Callable[[dict], None] | None, event: dict) -> None:
    """A callback must never break the run."""
    if on_event is None:
        return
    try:
        on_event(event)
    except Exception:
        logging.getLogger(__name__).exception("live event callback failed")


def _totals(out: Run, t0: float) -> dict:
    return {"turns": len(out.turns), "tokens": out.input_tokens + out.output_tokens,
            "cost_usd": float(out.cost_usd), "seconds": _now() - t0}


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
    system: str = AGENT_SYSTEM,
    tool_specs: list[dict] | None = None,
    max_turns: int | None = None,
    budget: Budget | None = None,
    on_event: Callable[[dict], None] | None = None,
    client: Any = None,
    output_model: type[BaseModel] = GroundedTriageResult,
    check: Callable[[BaseModel], None] | None = None,
    prompt: str | None = None,
) -> Run:
    if client is None:
        # built here, not at import, so tests never need a key
        client = anthropic.Anthropic()
    budget = budget or Budget.from_config()
    if check is None:
        # the scope check reads suspected_files, so it only fits triage-shaped models
        check = (tools.check_in_scope if issubclass(output_model, GroundedTriageResult)
                 else (lambda _result: None))
    schema = _schema_for(output_model)
    max_turns = max_turns or budget.max_turns
    t0 = _now()
    # None means the full tool set; [] means none, and the request then omits the key
    tool_specs = tools.TOOL_SPECS if tool_specs is None else tool_specs

    out = Run()
    # a stage passes its own prepared first message; everyone else gets the grounded prompt
    out.messages.append({"role": "user", "content":
                         prompt if prompt is not None else grounded_prompt(title, body, candidates)})

    def fail(exc: RunError, reason: str) -> RunError:
        event = {"type": "failed", "reason": reason, "message": str(exc),
                 "totals": _totals(out, t0)}
        if isinstance(exc, BudgetExceeded):
            event.update(limit=exc.limit, value=exc.value)
        _emit(on_event, event)
        return exc

    def over_budget() -> BudgetExceeded | None:
        totals = _totals(out, t0)
        # turns last: when the final turn also tripped another cap, name that cap
        checks = (("tokens", totals["tokens"], budget.max_tokens),
                  ("seconds", totals["seconds"], budget.max_seconds),
                  ("dollars", totals["cost_usd"], budget.max_dollars),
                  ("turns", totals["turns"], max_turns))
        for limit, used, cap in checks:
            if used >= cap:
                msg = (f"no final answer after {cap} turns" if limit == "turns"
                       else f"{limit} budget reached ({used} >= {cap})")
                return BudgetExceeded(msg, out, limit=limit, value=cap)
        return None

    for turn in range(1, max_turns + 1):
        if turn > 1 and (stop := over_budget()):
            raise fail(stop, "budget")
        _emit(on_event, {"type": "step_started", "turn": turn})
        started = time.perf_counter()
        try:
            request: dict[str, Any] = dict(
                model=C.ANTHROPIC_MODEL,
                max_tokens=C.MAX_TOKENS,
                system=system,
                output_config={"format": {"type": "json_schema",
                                          "schema": schema}},
                messages=out.messages,
            )
            if tool_specs:
                request["tools"] = tool_specs
            response = client.messages.create(**request)
        except anthropic.APIError as exc:
            # keep the turns already paid for
            raise fail(RunError(f"API error on turn {turn}: {exc}", out), "api") from exc
        out.turns.append(_record_turn(response, int((time.perf_counter() - started) * 1000)))
        last = out.turns[-1]
        _emit(on_event, {"type": "usage", "turn": turn,
                         "input_tokens": last.input_tokens,
                         "output_tokens": last.output_tokens,
                         "cost_usd": float(last.cost_usd),
                         "totals": _totals(out, t0)})

        if response.stop_reason == "tool_use":
            if not any(b.type == "tool_use" for b in response.content):
                raise fail(RunError("tool_use stop with no tool_use blocks", out), "run")
            # The whole content, thinking blocks included, exactly as received.
            out.messages.append({"role": "assistant", "content": response.content})
            results = []
            for block in response.content:
                if block.type != "tool_use":
                    continue
                _emit(on_event, {"type": "tool_called", "turn": turn,
                                 "tool_use_id": block.id, "name": block.name,
                                 "input": dict(block.input)})
                tool_start = time.perf_counter()
                output, is_error = tools.execute(block.name, block.input, cutoff=cutoff)
                duration_ms = int((time.perf_counter() - tool_start) * 1000)
                out.trace.append(ToolCall(
                    turn=turn, name=block.name, input=dict(block.input),
                    output=output[:2000], is_error=is_error,
                    duration_ms=duration_ms,
                ))
                _emit(on_event, {"type": "tool_returned", "turn": turn,
                                 "tool_use_id": block.id, "name": block.name,
                                 "output_preview": output[:2000],
                                 "is_error": is_error, "duration_ms": duration_ms})
                results.append({"type": "tool_result", "tool_use_id": block.id,
                                "content": output, "is_error": is_error})
            out.messages.append({"role": "user", "content": results})
            continue

        if response.stop_reason == "end_turn":
            # The closing turn belongs in the history too, even if it is rejected below.
            out.messages.append({"role": "assistant", "content": response.content})
            text = next((b.text for b in response.content if b.type == "text"), "")
            try:
                result = output_model.model_validate_json(text)
                check(result)
            except (ValidationError, ValueError) as exc:
                raise fail(RunError(f"final answer rejected: {exc}", out), "run") from exc
            out.result = result
            _emit(on_event, {"type": "done", "result": result.model_dump(),
                             "totals": _totals(out, t0)})
            return out

        raise fail(RunError(
            f"unexpected stop_reason {response.stop_reason!r} on turn {turn}", out), "run")

    # turns == max_turns here, so over_budget() is never None
    stop = over_budget()
    assert stop is not None
    raise fail(stop, "budget")
