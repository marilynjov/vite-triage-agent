"""The typed contract. One definition, three consumers.

This file is the single source of truth for Phase 02:

  * both providers constrain the model's output to the JSON Schema derived here
  * the route validates through Pydantic before anything leaves the backend
  * web/src/types.generated.ts is generated from it, so a field change here
    breaks the frontend build rather than production

Why the fields are what they are: `p2e` scores classification accuracy against
the GitHub labels the miner already captured, and 95% of rows carry at least
one. Vite's labels encode priority (`p2-edge-case`, `p3-minor-bug`,
`p4-important`) and area (`feat: css`, `feat: ssr`, `feat: hmr`), so severity
and components are both measurable for free. The vocabulary below stays generic
on purpose — the label mapping belongs to the eval, not to the contract, the
same way the miner stays repo-agnostic.
"""
from __future__ import annotations

from typing import Annotated, Any, Literal, Union

from pydantic import BaseModel, Field, TypeAdapter, field_validator

IssueType = Literal["bug", "regression", "enhancement", "documentation", "question", "upstream"]
Severity = Literal["low", "medium", "high"]


class TriageResult(BaseModel):
    """What a maintainer concludes from reading a bug report, as data."""

    model_config = {"extra": "forbid"}

    # FIRST on purpose. Grammar-constrained decoding emits properties in schema
    # order, so a trailing `reasoning` field explains a verdict the model has
    # already committed to. Leading, it is chain-of-thought that feeds them.
    # 4000, not 1000: as a leading field this is chain-of-thought, and a cap
    # sized for a trailing one-line justification truncates real reasoning. Still
    # capped — unbounded invites the same rambling that `maxItems` prevents, and
    # these are output tokens, the expensive kind. Note that NEITHER provider
    # enforces maxLength during decoding; Pydantic is what actually holds the
    # line, which is why Haiku's 1,200-char answer raised instead of leaking
    # through.
    reasoning: str = Field(max_length=4000)

    issue_type: IssueType
    severity: Severity

    # Bounded on purpose. A schema constrains shape, not length: left unbounded,
    # qwen2.5-coder filled this with eleven entries including "Web development
    # environment" and ran past max_tokens mid-string, producing invalid JSON.
    suspected_components: list[str] = Field(max_length=3)
    reproduction_steps: list[str] = Field(max_length=10)

    confidence: float = Field(ge=0.0, le=1.0)

    @field_validator("confidence", mode="before")
    @classmethod
    def _as_fraction(cls, value: Any) -> Any:
        """Accept 90 as 0.9.

        Ollama's grammar enforces structure but not numeric range, so a local
        model will happily return 90 against `maximum: 1`. One documented
        coercion at the boundary is the price of letting the frontend treat
        confidence as a guaranteed 0-1 float with no defensive code.
        """
        if isinstance(value, (int, float)) and value > 1:
            return value / 100
        return value


class GroundedTriageResult(TriageResult):
    """Phase 02's result, plus the files the fix touches — chosen from retrieval.

    A subclass, so TriageResult and every Phase 02 number stay exactly as they
    were. The model is shown retrieval's top candidates and may only pick among
    them; that rule depends on the request, so it cannot live in a static
    schema and is checked by api.triage.check_candidates instead.

    Most likely first, because it is scored as a ranking with the same recall@k
    and MRR as retrieval. Capped at 5: the ceiling is the candidates' own
    recall@10, so what this can buy is rank, not coverage.
    """

    suspected_files: list[str] = Field(max_length=5)


class ToolCall(BaseModel):
    """One tool call the agent made: what it asked, what came back, how long it took."""

    model_config = {"extra": "forbid"}

    turn: int
    name: str
    input: dict[str, Any]
    output: str = Field(max_length=2000)  # a preview; the full output went to the model
    is_error: bool
    duration_ms: int


def json_schema(model: type[BaseModel] = TriageResult) -> dict[str, Any]:
    """The schema both providers send, strict enough for either to accept.

    Anthropic's structured outputs and Ollama's grammar both want every property
    required and additionalProperties false. Pydantic emits `maxItems` from
    Field(max_length=...) for lists, which is what stops the rambling above.
    """
    schema = model.model_json_schema()
    schema["additionalProperties"] = False
    schema["required"] = sorted(schema["properties"].keys())
    return schema


# --- Phase 05 B+C: the live-run event stream -------------------------------
# Shared by the SSE endpoint and the UI reducer. Each model's JSON-Schema title
# is its class name so the generated TypeScript types carry exactly these names.

class _Live(BaseModel):
    model_config = {"extra": "forbid"}


class Totals(_Live):
    model_config = {"extra": "forbid", "title": "Totals"}
    turns: int
    tokens: int
    cost_usd: float
    seconds: float


class LiveCandidate(_Live):
    model_config = {"extra": "forbid", "title": "LiveCandidate"}
    path: str
    score: float
    start_line: int
    end_line: int


class RunStarted(_Live):
    model_config = {"extra": "forbid", "title": "RunStarted"}
    type: Literal["run_started"]
    seq: int
    candidates: list[LiveCandidate]


class StepStarted(_Live):
    model_config = {"extra": "forbid", "title": "StepStarted"}
    type: Literal["step_started"]
    seq: int
    turn: int


class Usage(_Live):
    model_config = {"extra": "forbid", "title": "Usage"}
    type: Literal["usage"]
    seq: int
    turn: int
    input_tokens: int
    output_tokens: int
    cost_usd: float
    totals: Totals


class ToolCalled(_Live):
    model_config = {"extra": "forbid", "title": "ToolCalled"}
    type: Literal["tool_called"]
    seq: int
    turn: int
    tool_use_id: str
    name: str
    input: dict[str, Any]


class ToolReturned(_Live):
    model_config = {"extra": "forbid", "title": "ToolReturned"}
    type: Literal["tool_returned"]
    seq: int
    turn: int
    tool_use_id: str
    name: str
    output_preview: str
    is_error: bool
    duration_ms: int


class Done(_Live):
    model_config = {"extra": "forbid", "title": "Done"}
    type: Literal["done"]
    seq: int
    result: GroundedTriageResult
    totals: Totals


def _require_all(schema: dict[str, Any]) -> None:
    # limit/value default to None (the loop omits them unless a budget stopped
    # the run), but the wire contract always carries them: required in the
    # schema keeps the TypeScript fields non-optional (`string | null`).
    schema["required"] = sorted(schema["properties"])


class Failed(_Live):
    model_config = {"extra": "forbid", "title": "Failed", "json_schema_extra": _require_all}
    type: Literal["failed"]
    seq: int
    reason: Literal["budget", "run", "api"]
    message: str
    totals: Totals
    limit: Literal["turns", "tokens", "seconds", "dollars"] | None = None
    value: float | None = None


LiveEvent = Annotated[
    Union[RunStarted, StepStarted, Usage, ToolCalled, ToolReturned, Done, Failed],
    Field(discriminator="type"),
]
LIVE_EVENT = TypeAdapter(LiveEvent)
