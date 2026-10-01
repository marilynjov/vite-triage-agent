"""Postgres, raw. No ORM.

You will write maybe six queries in this whole project and every one of them is
worth reading literally. An ORM would hide the one thing Phase 07's dashboard is
made of: what you actually recorded.
"""
from __future__ import annotations

from decimal import Decimal

from psycopg.rows import dict_row
from psycopg_pool import AsyncConnectionPool

from . import config as C

pool: AsyncConnectionPool | None = None


async def connect() -> None:
    """Open the pool and apply the schema. Called once, from the app lifespan."""
    global pool
    # dict_row for every query in the project: psycopg returns tuples by
    # default, and `dict(row)` on a tuple raises. Set it once here rather than
    # remembering it at each call site.
    pool = AsyncConnectionPool(
        C.DATABASE_URL,
        min_size=1,
        max_size=4,
        open=False,
        kwargs={"row_factory": dict_row},
    )
    await pool.open(wait=True, timeout=10)
    async with pool.connection() as conn:
        await conn.execute(C.SCHEMA_SQL.read_text())


async def disconnect() -> None:
    if pool is not None:
        await pool.close()


async def healthy() -> bool:
    """True if the database answers. /health lies if it doesn't check this."""
    if pool is None:
        return False
    async with pool.connection() as conn:
        row = await (await conn.execute("select 1 as ok")).fetchone()
        return row is not None and row["ok"] == 1


async def record_call(
    *,
    endpoint: str,
    provider: str,
    model: str,
    issue_number: int | None,
    usage,
    latency_ms: int,
    cost_usd: Decimal,
    stop_reason: str | None,
    request_id: str | None,
    error: str | None = None,
) -> int:
    """Insert one row into `calls` and return its id.
    """

    input_tokens = getattr(usage, "input_tokens", 0) or 0
    output_tokens = getattr(usage, "output_tokens", 0) or 0
    cache_creation_input_tokens = getattr(usage, "cache_creation_input_tokens", 0) or 0
    cache_read_input_tokens = getattr(usage, "cache_read_input_tokens", 0) or 0

    async with pool.connection() as conn:
        row = await (
            await conn.execute(
                """
                INSERT INTO calls (
                    endpoint,
                    provider,
                    model,
                    issue_number,
                    input_tokens,
                    output_tokens,
                    cache_read_input_tokens,
                    cache_creation_input_tokens,
                    latency_ms,
                    cost_usd,
                    stop_reason,
                    request_id,
                    error
                )
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                RETURNING id
                """,
                (
                    endpoint,
                    provider,
                    model,
                    issue_number,
                    input_tokens,
                    output_tokens,
                    cache_read_input_tokens,
                    cache_creation_input_tokens,
                    latency_ms,
                    cost_usd,
                    stop_reason,
                    request_id,
                    error,
                ),
            )
        ).fetchone()

    return row["id"] if row is not None else -1