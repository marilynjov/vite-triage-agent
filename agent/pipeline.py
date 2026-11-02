"""locate -> understand -> hypothesize: three calls to the agent loop under one budget."""
from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal

from api.schema import HypothesisResult, LocateResult, ToolCall, UnderstandResult
from retrieval.search import FileHit

from . import loop, stages
from .loop import Budget, Run, RunError, Turn

STAGE_TURNS = {"locate": 8, "understand": 6, "hypothesize": 1}
STAGES = tuple(STAGE_TURNS)


@dataclass
class PipelineRun:
    locate: LocateResult | None = None
    understand: UnderstandResult | None = None
    hypothesis: HypothesisResult | None = None
    runs: dict[str, Run] = field(default_factory=dict)
    failed_stage: str | None = None
    error: str | None = None
    seconds: float = 0.0

    @property
    def result(self) -> HypothesisResult | None:
        return self.hypothesis

    @property
    def turns(self) -> list[Turn]:
        return [t for r in self.runs.values() for t in r.turns]

    @property
    def trace(self) -> list[ToolCall]:
        return [c for r in self.runs.values() for c in r.trace]

    @property
    def input_tokens(self) -> int:
        return sum(r.input_tokens for r in self.runs.values())

    @property
    def output_tokens(self) -> int:
        return sum(r.output_tokens for r in self.runs.values())

    @property
    def cost_usd(self) -> Decimal:
        return sum((r.cost_usd for r in self.runs.values()), Decimal("0"))

    @property
    def stage_costs(self) -> dict[str, float]:
        return {s: float(r.cost_usd) for s, r in self.runs.items()}


class PipelineError(RunError):
    """A stage failed. The message is the stage's own; `.stage` says which."""

    def __init__(self, message: str, run: PipelineRun, stage: str):
        super().__init__(message, run)
        self.stage = stage


def run_pipeline(title: str, body: str, candidates: list[FileHit], cutoff: str, *,
                 budget: Budget | None = None, client=None) -> PipelineRun:
    budget = budget or Budget.from_config()
    pr = PipelineRun()

    def fail(stage: str, message: str, partial: Run | None = None) -> PipelineError:
        if partial is not None:
            pr.runs[stage] = partial  # its spend counts toward the totals
        pr.failed_stage, pr.error = stage, message
        return PipelineError(message, pr, stage)

    def stage_budget(stage: str) -> Budget | None:
        b = Budget(
            max_turns=STAGE_TURNS[stage],
            max_tokens=budget.max_tokens - (pr.input_tokens + pr.output_tokens),
            max_seconds=budget.max_seconds - pr.seconds,
            max_dollars=budget.max_dollars - float(pr.cost_usd),
        )
        return b if min(b.max_tokens, b.max_seconds, b.max_dollars) > 0 else None

    def call(stage: str, **kw) -> Run:
        b = stage_budget(stage)
        if b is None:
            raise fail(stage, f"budget exhausted before the {stage} stage")
        t0 = loop._now()
        try:
            return loop.run(title, body, candidates, cutoff, budget=b, client=client,
                            max_turns=b.max_turns, **kw)
        except RunError as exc:
            raise fail(stage, str(exc), exc.run) from exc
        finally:
            pr.seconds += loop._now() - t0

    r = call("locate", system=stages.LOCATE_SYSTEM, tool_specs=stages.LOCATE_TOOLS,
             output_model=LocateResult, check=stages.check_locate())
    pr.runs["locate"], pr.locate = r, r.result

    r = call("understand", system=stages.UNDERSTAND_SYSTEM, tool_specs=stages.UNDERSTAND_TOOLS,
             output_model=UnderstandResult, check=stages.check_understand(pr.locate),
             prompt=stages.understand_prompt(title, body, pr.locate))
    pr.runs["understand"], pr.understand = r, r.result

    r = call("hypothesize", system=stages.HYPOTHESIZE_SYSTEM, tool_specs=stages.HYPOTHESIZE_TOOLS,
             output_model=HypothesisResult, check=stages.check_hypothesis(pr.locate, pr.understand),
             prompt=stages.hypothesize_prompt(title, body, pr.locate, pr.understand))
    pr.runs["hypothesize"], pr.hypothesis = r, r.result
    return pr
