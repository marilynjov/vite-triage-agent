"""The wire contract.

Phase 01 returns prose deliberately — the naive baseline has no structure to
speak of, and feeling that limitation is what motivates Phase 02. When you get
there, `TriageResult` replaces `answer: str` and is generated into TypeScript so
the backend and the UI share one definition.
"""
from __future__ import annotations

from pydantic import BaseModel, Field

from .schema import TriageResult


class TriageRequest(BaseModel):
    title: str = Field(min_length=1)
    body: str = Field(min_length=1)
    # Optional, and only ever for own bookkeeping: it lets you join a call
    # back to a dataset row. Never put it in the prompt — the model must not know
    # which issue this is, or you are grading it on memorised GitHub history.
    issue_number: int | None = None


class TriageResponse(BaseModel):
    answer: str
    call_id: int
    model: str
    provider: str
    input_tokens: int
    output_tokens: int
    latency_ms: int
    cost_usd: float


class StructuredTriageResponse(BaseModel):
    """Phase 02. Note what is NOT here: no `answer: str`, no optional fields.

    `result` is a TriageResult or the request failed. That is the whole point —
    the frontend gets a guaranteed shape, which is what lets it render without
    a single defensive optional-chain.
    """

    result: TriageResult
    call_id: int
    model: str
    provider: str
    input_tokens: int
    output_tokens: int
    latency_ms: int
    cost_usd: float
