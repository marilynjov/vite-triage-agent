"""python -m uvicorn api.main:app --reload

Phase 01: /health, one triage endpoint, and a row in `calls` for every call.
"""
from __future__ import annotations

from dataclasses import asdict
from decimal import Decimal
import asyncio
import json
import logging
import time
import uuid
from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import StreamingResponse

from agent import loop as agent_loop, tools as agent_tools
from retrieval import config as R, search
from fastapi.middleware.cors import CORSMiddleware
from starlette.concurrency import run_in_threadpool

from . import config as C, cost, db, live, triage
from .models import (
    AgentTriageRequest,
    AgentTriageResponse,
    GroundedTriageResponse,
    RetrievedFile,
    RetrieveRequest,
    RetrieveResponse,
    StructuredTriageResponse,
    TriageRequest,
    TriageResponse,
)


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


@app.post("/triage/structured", response_model=StructuredTriageResponse)
async def triage_structured(request: TriageRequest) -> StructuredTriageResponse:
    """Phase 02. Same accounting as /triage/naive, guaranteed shape out.

    The `except` here carries more weight than the naive route's. Constrained
    decoding guarantees structure but not value constraints, so this call can
    fail on the contract itself — a `reasoning` over maxLength, more
    reproduction_steps than maxItems — which happened three times across 80 eval
    calls. Turning that into a 500 is the right answer: a malformed TriageResult
    must never reach the browser, because the frontend has stopped defending
    against one.
    """
    started = time.perf_counter()
    try:
        completion = triage.triage_structured(request.title, request.body)
    except Exception as exc:
        latency_ms = int((time.perf_counter() - started) * 1000)
        await db.record_call(
            endpoint="/triage/structured",
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
        raise HTTPException(status_code=500, detail=str(exc)) from exc

    latency_ms = int((time.perf_counter() - started) * 1000)
    cost_usd = cost.compute_cost(completion)

    call_id = await db.record_call(
        endpoint="/triage/structured",
        provider=completion.provider,
        model=completion.model,
        issue_number=request.issue_number,
        usage=completion,
        latency_ms=latency_ms,
        cost_usd=cost_usd,
        stop_reason=completion.stop_reason,
        request_id=completion.request_id,
    )

    return StructuredTriageResponse(
        result=completion.parsed,
        call_id=call_id,
        model=completion.model,
        provider=completion.provider,
        input_tokens=completion.input_tokens,
        output_tokens=completion.output_tokens,
        latency_ms=latency_ms,
        cost_usd=float(cost_usd),
    )


@app.post("/triage/grounded", response_model=GroundedTriageResponse)
async def triage_grounded(request: TriageRequest) -> GroundedTriageResponse:
    """Retrieval, then structured triage that picks among what it found.

    Its own endpoint, not a flag on /triage/structured, so Phase 02's numbers
    and its `calls` rows stay comparable. The query is built exactly as
    /retrieve and the harness build it. Search and the model call are both
    synchronous, so they run in a thread rather than on the event loop.

    A suspected file outside the candidates fails the request like any other
    contract violation, and is recorded as one.
    """
    corpus, strategy = R.CORPUS, R.DEFAULT_STRATEGY
    started = time.perf_counter()
    candidates = await run_in_threadpool(
        search.search_ranked, f"{request.title}\n\n{request.body}",
        strategy=strategy, k=C.GROUNDED_K, corpus=corpus,
    )
    if not candidates:
        raise HTTPException(404, f"no index for {corpus} ({strategy})")
    try:
        completion = await run_in_threadpool(
            triage.triage_grounded, request.title, request.body, candidates
        )
    except Exception as exc:
        latency_ms = int((time.perf_counter() - started) * 1000)
        await db.record_call(
            endpoint="/triage/grounded",
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
        raise HTTPException(status_code=500, detail=str(exc)) from exc

    latency_ms = int((time.perf_counter() - started) * 1000)
    cost_usd = cost.compute_cost(completion)

    call_id = await db.record_call(
        endpoint="/triage/grounded",
        provider=completion.provider,
        model=completion.model,
        issue_number=request.issue_number,
        usage=completion,
        latency_ms=latency_ms,
        cost_usd=cost_usd,
        stop_reason=completion.stop_reason,
        request_id=completion.request_id,
    )

    return GroundedTriageResponse(
        result=completion.parsed,
        candidates=[
            RetrievedFile(path=f.path, score=round(f.score, 4), start_line=f.best.start_line,
                          end_line=f.best.end_line, snippet=f.best.text)
            for f in candidates
        ],
        corpus=corpus,
        strategy=strategy,
        call_id=call_id,
        model=completion.model,
        provider=completion.provider,
        input_tokens=completion.input_tokens,
        output_tokens=completion.output_tokens,
        latency_ms=latency_ms,
        cost_usd=float(cost_usd),
    )


async def record_run_turns(run, *, run_id: str, endpoint: str,
                           issue_number: int | None, error: str | None = None) -> None:
    """One `calls` row per turn of an agent run; the error rides on the last."""
    for i, t in enumerate(run.turns, start=1):
        await db.record_call(
            endpoint=endpoint,
            provider="anthropic",
            model=C.ANTHROPIC_MODEL,
            issue_number=issue_number,
            usage=t,
            latency_ms=t.latency_ms,
            cost_usd=t.cost_usd,
            stop_reason=t.stop_reason,
            request_id=t.request_id,
            error=error if i == len(run.turns) else None,
            run_id=run_id,
            turn=i,
        )


@app.post("/triage/agent", response_model=AgentTriageResponse)
async def triage_agent(request: AgentTriageRequest) -> AgentTriageResponse:
    """Retrieval, then a tool-using agent that may open files before answering.

    One `calls` row per API turn, all sharing a run_id, so a run's cost is a
    GROUP BY. A failed run still records the turns it paid for, the last one
    carrying the error.
    """
    if C.PROVIDER != "anthropic":
        raise HTTPException(400, "/triage/agent needs PROVIDER=anthropic (tool use is Claude-only)")
    corpus, strategy = R.CORPUS, R.DEFAULT_STRATEGY
    started = time.perf_counter()
    candidates = await run_in_threadpool(
        search.search_ranked, f"{request.title}\n\n{request.body}",
        strategy=strategy, k=C.GROUNDED_K, corpus=corpus,
    )
    if not candidates:
        raise HTTPException(404, f"no index for {corpus} ({strategy})")
    run_id = uuid.uuid4().hex
    cutoff = (request.created_at.isoformat() if request.created_at
              else agent_tools.pinned_date())

    try:
        run = await run_in_threadpool(
            agent_loop.run, request.title, request.body, candidates, cutoff
        )
    except Exception as exc:
        failed = getattr(exc, "run", None)
        if failed is not None and failed.turns:
            await record_run_turns(failed, run_id=run_id, endpoint="/triage/agent",
                                   issue_number=request.issue_number, error=str(exc))
        else:
            await db.record_call(
                endpoint="/triage/agent",
                provider="anthropic",
                model=C.ANTHROPIC_MODEL,
                issue_number=request.issue_number,
                usage=None,
                latency_ms=int((time.perf_counter() - started) * 1000),
                cost_usd=Decimal("0"),
                stop_reason=None,
                request_id=None,
                error=str(exc),
                run_id=run_id,
                turn=1,
            )
        raise HTTPException(status_code=500, detail=str(exc)) from exc

    await record_run_turns(run, run_id=run_id, endpoint="/triage/agent",
                           issue_number=request.issue_number)
    return AgentTriageResponse(
        result=run.result,
        candidates=[
            RetrievedFile(path=f.path, score=round(f.score, 4), start_line=f.best.start_line,
                          end_line=f.best.end_line, snippet=f.best.text)
            for f in candidates
        ],
        trace=run.trace,
        turns=len(run.turns),
        run_id=run_id,
        corpus=corpus,
        strategy=strategy,
        model=C.ANTHROPIC_MODEL,
        provider="anthropic",
        input_tokens=run.input_tokens,
        output_tokens=run.output_tokens,
        latency_ms=int((time.perf_counter() - started) * 1000),
        cost_usd=float(run.cost_usd),
    )


@app.post("/retrieve", response_model=RetrieveResponse)
def retrieve(request: RetrieveRequest) -> RetrieveResponse:
    """Phase 03 behind HTTP: issue in, the files to open out, no model involved.

    The query is built exactly as eval/retrieve.py builds it — title, blank
    line, body — so what this returns is what recall@k measured. A plain `def`,
    not `async`, because search is synchronous psycopg and embedding; FastAPI
    runs it in a thread instead of blocking the event loop.
    """
    corpus = request.corpus or R.CORPUS
    strategy = request.strategy or R.DEFAULT_STRATEGY
    started = time.perf_counter()
    hits = search.search_ranked(
        f"{request.title}\n\n{request.body}",
        strategy=strategy, k=request.k, corpus=corpus,
    )
    if not hits:
        # Unindexed looks identical to "nothing relevant" unless we say so.
        have = [f"{c} ({s})" for c, s, _ in search.indexed()]
        raise HTTPException(404, f"no index for {corpus} ({strategy}); indexed: {have}")
    return RetrieveResponse(
        corpus=corpus,
        strategy=strategy,
        files=[
            RetrievedFile(path=f.path, score=round(f.score, 4), start_line=f.best.start_line,
                          end_line=f.best.end_line, snippet=f.best.text)
            for f in hits
        ],
        latency_ms=int((time.perf_counter() - started) * 1000),
    )


@app.get("/corpora")
def corpora() -> list[dict]:
    """What /retrieve can search."""
    return [{"corpus": c, "strategy": s, "chunks": n} for c, s, n in search.indexed()]


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

# ---- Phase 05 B+C: live runs ------------------------------------------------

log = logging.getLogger(__name__)
_tasks: set[asyncio.Task] = set()  # belt and braces: a task must never be GC'd mid-run


def format_sse(event: dict) -> str:
    # json.dumps escapes newlines, so the data line is always one line.
    return f"id: {event['seq']}\nevent: {event['type']}\ndata: {json.dumps(event)}\n\n"


def _totals_of(run) -> dict:
    return {"turns": len(run.turns) if run else 0,
            "tokens": (run.input_tokens + run.output_tokens) if run else 0,
            "cost_usd": float(run.cost_usd) if run else 0.0,
            "seconds": 0.0}


def _append_failed(run_id: str, exc: BaseException | None, run, started: float) -> str:
    """Store the failed event the loop could not (or did not) leave behind."""
    totals = _totals_of(run)
    totals["seconds"] = round(time.perf_counter() - started, 3)
    event: dict = {"type": "failed", "totals": totals}
    if exc is None:
        event.update(reason="api", message="run ended without a final event")
    elif isinstance(exc, agent_loop.BudgetExceeded):
        event.update(reason="budget", message=str(exc), limit=exc.limit, value=exc.value)
    elif isinstance(exc, agent_loop.RunError):
        reason = "api" if str(exc).startswith("API error") else "run"
        event.update(reason=reason, message=str(exc))
    else:
        event.update(reason="api", message=str(exc) or type(exc).__name__)
    try:
        live.registry.append(run_id, event)
    except (KeyError, ValueError):
        # evicted, or a terminal event landed meanwhile (or a contract mismatch)
        log.exception("could not record fallback failed event for run %s", run_id)
    return event["message"]


def _is_running(run_id: str) -> bool:
    snap = live.registry.get(run_id)
    return snap is not None and snap.status == "running"


async def _execute(run_id: str, request: AgentTriageRequest, cutoff: str) -> None:
    """The server-owned task. Whatever happens, the run ends with one done or failed."""
    started = time.perf_counter()
    run = None
    exc: BaseException | None = None
    try:
        candidates = await run_in_threadpool(
            search.search_ranked, f"{request.title}\n\n{request.body}",
            strategy=R.DEFAULT_STRATEGY, k=C.GROUNDED_K, corpus=R.CORPUS,
        )
        if not candidates:
            raise RuntimeError(f"no index for {R.CORPUS} ({R.DEFAULT_STRATEGY})")
        live.registry.append(run_id, {
            "type": "run_started",
            "candidates": [{"path": f.path, "score": round(f.score, 4),
                            "start_line": f.best.start_line, "end_line": f.best.end_line}
                           for f in candidates],
        })
        run = await run_in_threadpool(
            lambda: agent_loop.run(
                request.title, request.body, candidates, cutoff,
                on_event=lambda e: live.registry.append(run_id, e)),
        )
    except BaseException as e:  # noqa: BLE001 - a crash must still end the run
        exc = e
        run = getattr(e, "run", None)
        if not isinstance(e, Exception):
            # CancelledError, KeyboardInterrupt, SystemExit: end the run, then propagate.
            _append_failed(run_id, e, run, started)
            raise
        if not isinstance(e, agent_loop.RunError):
            log.exception("live run %s crashed", run_id)
    error = str(exc) if exc else None
    if _is_running(run_id):
        # The loop returned (or died) without a terminal event stored.
        error = _append_failed(run_id, exc, run, started) or error
    try:
        if run is not None and run.turns:
            await record_run_turns(run, run_id=run_id, endpoint="/runs",
                                   issue_number=request.issue_number, error=error)
        elif error is not None:
            await db.record_call(
                endpoint="/runs", provider="anthropic", model=C.ANTHROPIC_MODEL,
                issue_number=request.issue_number, usage=None,
                latency_ms=int((time.perf_counter() - started) * 1000),
                cost_usd=Decimal("0"), stop_reason=None, request_id=None,
                error=error, run_id=run_id, turn=1,
            )
    except Exception:
        # accounting trouble must not undo a finished run, but must not vanish
        log.exception("recording calls rows for live run %s failed", run_id)


@app.post("/runs", status_code=202)
async def start_run(request: AgentTriageRequest) -> dict:
    """Start a live agent run in a task the server owns; watch it on /runs/{id}/events."""
    if C.PROVIDER != "anthropic":
        raise HTTPException(400, "/runs needs PROVIDER=anthropic (tool use is Claude-only)")
    cutoff = (request.created_at.isoformat() if request.created_at
              else agent_tools.pinned_date())
    try:
        run_id = live.registry.start()
    except live.RegistryFull as exc:
        raise HTTPException(429, str(exc)) from exc
    task = asyncio.create_task(_execute(run_id, request, cutoff))
    _tasks.add(task)
    task.add_done_callback(_tasks.discard)
    live.registry.set_task(run_id, task)
    return {"run_id": run_id}


# Declared before /runs/{run_id}, or "limits" would be read as a run id.
@app.get("/runs/limits")
async def run_limits() -> dict:
    return asdict(agent_loop.Budget.from_config())


@app.get("/runs/{run_id}")
async def run_snapshot(run_id: str) -> dict:
    snap = live.registry.get(run_id)
    if snap is None:
        raise HTTPException(404, "unknown or expired run")
    last = snap.events[-1] if snap.events else None
    return {
        "run_id": run_id,
        "status": snap.status,
        "last_seq": last["seq"] if last else 0,
        "result": last["result"] if snap.status == "done" else None,
        "failure": last if snap.status == "failed" else None,
    }


@app.get("/runs/{run_id}/events")
async def run_events(run_id: str, request: Request, after: int = 0):
    if live.registry.get(run_id) is None:
        raise HTTPException(404, "unknown or expired run")
    header = request.headers.get("last-event-id")
    try:
        seq = int(header) if header is not None else after
    except ValueError:
        seq = after

    async def stream():
        nonlocal seq
        while True:
            # wait_after blocks on a Condition: never on the event loop.
            events = await run_in_threadpool(
                live.registry.wait_after, run_id, seq, C.LIVE_KEEPALIVE_SECONDS)
            for e in events:
                seq = e["seq"]
                yield format_sse(e)
                if e["type"] in ("done", "failed"):
                    return
            if not events:
                if not _is_running(run_id):
                    return  # finished (or expired) with nothing newer to send
                yield ": keep-alive\n\n"

    return StreamingResponse(stream(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache"})
