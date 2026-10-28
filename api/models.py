"""The wire contract.

Phase 01 returns prose deliberately — the naive baseline has no structure to
speak of, and feeling that limitation is what motivates Phase 02. When you get
there, `TriageResult` replaces `answer: str` and is generated into TypeScript so
the backend and the UI share one definition.
"""
from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, Field

from .schema import GroundedTriageResult, ToolCall, TriageResult


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


class RetrieveRequest(BaseModel):
    """Retrieval only: no model call, so no cost and no row in `calls`."""
    title: str = Field(min_length=1)
    body: str = ""
    # owner/name of an indexed repo. None means the one ENV_FILE points at.
    corpus: str | None = None
    strategy: str | None = None
    k: int = Field(default=10, ge=1, le=50)


class RetrievedFile(BaseModel):
    path: str
    score: float
    # The chunk that ranked this file, so the UI can show why it is here.
    start_line: int
    end_line: int
    snippet: str


class RetrieveResponse(BaseModel):
    corpus: str
    strategy: str
    files: list[RetrievedFile]
    latency_ms: int


class GroundedTriageResponse(BaseModel):
    """Structured triage that was shown retrieval's candidates first.

    `candidates` is what the model chose from, in retrieval's order, so a
    reader can compare the two rankings side by side.
    """

    result: GroundedTriageResult
    candidates: list[RetrievedFile]
    corpus: str
    strategy: str
    call_id: int
    model: str
    provider: str
    input_tokens: int
    output_tokens: int
    latency_ms: int
    cost_usd: float



class AgentTriageRequest(TriageRequest):
    # The agent's tools see the repo as of this date. Omitted means the pin date.
    created_at: datetime | None = None


class AgentTriageResponse(BaseModel):
    """Grounded triage by a tool-using agent. `trace` is what it looked at."""

    result: GroundedTriageResult
    candidates: list[RetrievedFile]
    trace: list[ToolCall]
    turns: int
    run_id: str
    corpus: str
    strategy: str
    model: str
    provider: str
    input_tokens: int
    output_tokens: int
    latency_ms: int
    cost_usd: float
