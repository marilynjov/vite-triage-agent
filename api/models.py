"""The wire contract.

Phase 01 returns prose deliberately — the naive baseline has no structure to
speak of, and feeling that limitation is what motivates Phase 02. When you get
there, `TriageResult` replaces `answer: str` and is generated into TypeScript so
the backend and the UI share one definition.
"""
from __future__ import annotations

from pydantic import BaseModel, Field


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
