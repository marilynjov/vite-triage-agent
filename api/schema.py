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

from typing import Any, Literal

from pydantic import BaseModel, Field, field_validator

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


def json_schema() -> dict[str, Any]:
    """The schema both providers send, strict enough for either to accept.

    Anthropic's structured outputs and Ollama's grammar both want every property
    required and additionalProperties false. Pydantic emits `maxItems` from
    Field(max_length=...) for lists, which is what stops the rambling above.
    """
    schema = TriageResult.model_json_schema()
    schema["additionalProperties"] = False
    schema["required"] = sorted(schema["properties"].keys())
    return schema
