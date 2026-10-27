"""Query the index. Chunks in, ranked file paths out.

    python -m retrieval.search "less @import fails in dev"
    ENV_FILE=.env.bowling python -m retrieval.search "score shows the wrong total" --k 5

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


@dataclass(frozen=True)
class FileHit:
    """A file, scored by its best chunk — and that chunk, so a person can see why."""
    path: str
    score: float
    best: Hit


def search_chunks(
    query: str, *, strategy: str = "fixed_v1", k: int = 50, corpus: str | None = None
) -> list[Hit]:
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
            (vector, corpus or R.CORPUS, strategy, vector, k),
        ).fetchall()
    return [Hit(p, float(s), sl, el, t) for p, sl, el, t, s in rows]


def search_ranked(
    query: str, *, strategy: str = "fixed_v1", k: int = 10, corpus: str | None = None
) -> list[FileHit]:
    """Ranked distinct files, each with the chunk that put it there."""
    # Over-fetch chunks: 10 files can easily need 60 chunks when one big file
    # dominates the top of the list.
    best: dict[str, Hit] = {}
    for hit in search_chunks(query, strategy=strategy, k=k * 12, corpus=corpus):
        if hit.path not in best or hit.score > best[hit.path].score:
            best[hit.path] = hit
    ranked = sorted(best.values(), key=lambda h: -h.score)[:k]
    return [FileHit(h.path, h.score, h) for h in ranked]


def search_files(
    query: str, *, strategy: str = "fixed_v1", k: int = 10, corpus: str | None = None
) -> list[str]:
    """Ranked distinct file paths, best chunk first. This is what recall@k scores."""
    return [f.path for f in search_ranked(query, strategy=strategy, k=k, corpus=corpus)]


def indexed() -> list[tuple[str, str, int]]:
    """(corpus, strategy, chunk count) for everything in the table."""
    with psycopg.connect(R.DATABASE_URL) as conn:
        return conn.execute(
            "select corpus, strategy, count(*) from chunks group by 1, 2 order by 1, 2"
        ).fetchall()


def main() -> None:
    import argparse

    ap = argparse.ArgumentParser(description="Paste an issue, get the files to open.")
    ap.add_argument("query", help="issue title and/or body, as a reporter wrote it")
    ap.add_argument("--corpus", default=R.CORPUS, help="owner/name (default: from ENV_FILE)")
    ap.add_argument("--strategy", default=R.DEFAULT_STRATEGY)
    ap.add_argument("--k", type=int, default=10)
    args = ap.parse_args()

    hits = search_ranked(args.query, strategy=args.strategy, k=args.k, corpus=args.corpus)
    if not hits:
        # The commonest cause is an index that was never built for this pair,
        # which otherwise looks exactly like "nothing relevant".
        print(f"no chunks for corpus={args.corpus} strategy={args.strategy}. Indexed:")
        for corpus, strategy, n in indexed():
            print(f"  {corpus:<28} {strategy:<18} {n:>6,} chunks")
        raise SystemExit(1)

    print(f"corpus {args.corpus}   strategy {args.strategy}\n")
    for rank, f in enumerate(hits, 1):
        print(f"{rank:>3}. {f.score:.3f}  {f.path}:{f.best.start_line}-{f.best.end_line}")


if __name__ == "__main__":
    main()
