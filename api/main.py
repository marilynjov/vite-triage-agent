"""python -m uvicorn api.main:app --reload

Phase 01: /health, one triage endpoint, and a row in `calls` for every call.
"""
from __future__ import annotations

from decimal import Decimal
import time
from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware

from . import config as C, cost, db, triage
from .models import TriageRequest, TriageResponse


@asynccontextmanager
async def lifespan(app: FastAPI):
    await db.connect()
    yield
    await db.disconnect()


app = FastAPI(title="Vite Triage Agent", lifespan=lifespan)

# The Vite dev server is a different origin. Narrow, not "*", so you never have
# to wonder later whether this was left open.
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173"],
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.get("/health")
async def health() -> dict:
    """Green means the process is up AND the database answers."""
    ok = await db.healthy()
    if not ok:
        raise HTTPException(503, "database unreachable")
    return {"status": "ok", "provider": C.PROVIDER, "model": C.active_model()}


@app.post("/triage/naive", response_model=TriageResponse)
async def triage_naive(request: TriageRequest) -> TriageResponse:
    """One model call, prose out, every call accounted for.
    """
    started = time.perf_counter()
    try:
        completion = triage.triage_naive(request.title, request.body)
    except Exception as exc:
        # A failed call still costs latency and still belongs in the table.
        latency_ms = int((time.perf_counter() - started) * 1000)

        await db.record_call(
            endpoint="/triage/naive",
            provider=C.PROVIDER,
            model=C.active_model(),
            issue_number=None,
            usage=None,
            latency_ms=latency_ms,
            cost_usd=Decimal("0"),
            stop_reason=None,
            request_id=None,
            error=str(exc),
        )

        raise HTTPException(
            status_code=500,
            detail=str(exc),
        ) from exc

    latency_ms = int((time.perf_counter() - started) * 1000)

    cost_usd = cost.compute_cost(completion)

    call_id = await db.record_call(
        endpoint="/triage/naive",
        provider=completion.provider,
        model=completion.model,
        issue_number=request.issue_number,
        usage=completion,
        latency_ms=latency_ms,
        cost_usd=cost_usd,
        stop_reason=completion.stop_reason,
        request_id=completion.request_id,
    )

    return TriageResponse(
        answer=completion.text,
        call_id=call_id,
        model=completion.model,
        provider=completion.provider,
        input_tokens=completion.input_tokens,
        output_tokens=completion.output_tokens,
        latency_ms=latency_ms,
        cost_usd=float(cost_usd),
    )


@app.get("/calls")
async def recent_calls(limit: int = 20) -> list[dict]:
    """Your own logs, over HTTP, because p1e is 'read your own logs'.

    """
    async with db.pool.connection() as conn:
        row = await (
            await conn.execute(
                """
                SELECT *
                FROM calls
                ORDER BY created_at DESC
                LIMIT %s
                """,
                (limit,),
            )
        ).fetchall()
    return [dict(r) for r in row]   