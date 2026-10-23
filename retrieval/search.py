"""Query the index. Chunks in, ranked file paths out.

The harness scores **files**, because gold labels are files. So a query returns
chunks, and the chunks collapse to a ranked file list by their best-scoring
chunk. That collapse is a real modelling choice: a file whose single best chunk
scores 0.7 ranks above a file with four chunks at 0.6, which is usually right
for localisation and is worth revisiting if the failure analysis says otherwise.
"""
from __future__ import annotations

from dataclasses import dataclass

import psycopg

from . import config as R
from . import embed


@dataclass(frozen=True)
class Hit:
    path: str
    score: float          # cosine similarity, 1.0 = identical
    start_line: int
    end_line: int
    text: str


def search_chunks(query: str, *, strategy: str = "fixed_v1", k: int = 50) -> list[Hit]:
    vector = str(embed.embed_query(query))
    with psycopg.connect(R.DATABASE_URL) as conn:
        rows = conn.execute(
            """
            select path, start_line, end_line, text, 1 - (embedding <=> %s::vector)
            from chunks
            where corpus = %s and strategy = %s
            order by embedding <=> %s::vector
            limit %s
            """,
            (vector, R.CORPUS, strategy, vector, k),
        ).fetchall()
    return [Hit(p, float(s), sl, el, t) for p, sl, el, t, s in rows]


def search_files(query: str, *, strategy: str = "fixed_v1", k: int = 10) -> list[str]:
    """Ranked distinct file paths, best chunk first. This is what recall@k scores."""
    # Over-fetch chunks: 10 files can easily need 60 chunks when one big file
    # dominates the top of the list.
    seen: dict[str, float] = {}
    for hit in search_chunks(query, strategy=strategy, k=k * 12):
        if hit.path not in seen or hit.score > seen[hit.path]:
            seen[hit.path] = hit.score
    return [p for p, _ in sorted(seen.items(), key=lambda kv: -kv[1])][:k]
