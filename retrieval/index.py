"""Build the chunk index.

    python -m retrieval.index                 # fixed_v1
    python -m retrieval.index --strategy fixed_v1 --rebuild

Reads files at the **pinned SHA**, not the working tree. The whole project is
evaluated against one commit of Vite; indexing today's checkout would mean the
index and the gold labels describe different codebases.
"""
from __future__ import annotations

import argparse
import subprocess
import time

import psycopg

from mining import config as M
from . import chunk as K
from . import config as R
from . import embed


def _git(*args: str) -> str:
    return subprocess.run(
        ["git", "-C", str(M.REPO_PATH), *args],
        capture_output=True, text=True, check=True,
    ).stdout


def in_scope_files(sha: str) -> list[str]:
    """The same gold-label scope the miner used. One definition, two consumers."""
    paths = _git("ls-tree", "-r", "--name-only", sha).splitlines()
    return sorted(p for p in paths if M.is_gold_candidate(p))


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--strategy", default="fixed_v1", choices=sorted(K.STRATEGIES))
    ap.add_argument("--rebuild", action="store_true",
                    help="delete this strategy's existing chunks first")
    args = ap.parse_args()

    if not M.PINNED_SHA.exists():
        raise SystemExit("no pinned sha — run `python -m mining commits` first")
    sha = M.PINNED_SHA.read_text().strip()

    files = in_scope_files(sha)
    print(f"corpus {R.CORPUS}   strategy {args.strategy}   pinned {sha[:10]}   "
          f"{len(files)} files in scope   lang {R.TREE_SITTER_LANGUAGE or '(none)'}")

    chunker = K.STRATEGIES[args.strategy]
    chunks: list[K.Chunk] = []
    for path in files:
        chunks.extend(chunker(path, _git("show", f"{sha}:{path}")))
    print(f"chunks           {len(chunks):,}")

    started = time.perf_counter()
    vectors = embed.embed_documents([c.text for c in chunks])
    print(f"embedded         {len(vectors):,} in {time.perf_counter() - started:.0f}s")

    with psycopg.connect(R.DATABASE_URL) as conn:
        if args.rebuild:
            n = conn.execute(
                "delete from chunks where corpus = %s and strategy = %s",
                (R.CORPUS, args.strategy),
            ).rowcount
            print(f"deleted          {n:,} existing rows")
        with conn.cursor() as cur:
            cur.executemany(
                "insert into chunks (corpus, strategy, path, start_line, end_line,"
                " text, embedding) values (%s, %s, %s, %s, %s, %s, %s)",
                [(R.CORPUS, args.strategy, c.path, c.start_line, c.end_line,
                  c.text, str(v)) for c, v in zip(chunks, vectors)],
            )
        conn.commit()

    print(f"→ {len(chunks):,} rows in chunks, corpus={R.CORPUS} strategy={args.strategy}")


if __name__ == "__main__":
    main()
