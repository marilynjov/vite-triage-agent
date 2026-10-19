"""Chunking. v1 is fixed windows; v2 will split on syntax.

Both strategies return the same shape, so the indexer and the harness never
learn which one produced a chunk. That is what makes the v1-vs-v2 comparison a
config change rather than a rewrite.
"""
from __future__ import annotations

from dataclasses import dataclass

from . import config as R


@dataclass(frozen=True)
class Chunk:
    path: str
    start_line: int
    end_line: int
    text: str


def fixed_windows(path: str, source: str) -> list[Chunk]:
    """v1: slide a fixed character window over the file.

    Deliberately bad, and worth understanding *why* before Phase 03's v2:

      * it cuts functions in half, so the thing you are matching against is
        split across two chunks and whole in neither
      * a chunk carries no indication of where it came from — no file path, no
        imports, no enclosing class — so an embedding of `if (!id) return` is
        indistinguishable between twenty files
      * the overlap papers over the first problem at the cost of indexing the
        same text twice

    Line numbers are derived rather than tracked, because a character window has
    no concept of a line. That mismatch is itself part of the argument for v2.
    """
    chunks: list[Chunk] = []
    step = max(R.CHUNK_CHARS - R.CHUNK_OVERLAP, 1)

    for start in range(0, max(len(source), 1), step):
        window = source[start : start + R.CHUNK_CHARS]
        if not window.strip():
            continue
        start_line = source.count("\n", 0, start) + 1
        chunks.append(
            Chunk(
                path=path,
                start_line=start_line,
                end_line=start_line + window.count("\n"),
                text=window,
            )
        )
        if start + R.CHUNK_CHARS >= len(source):
            break

    return chunks


STRATEGIES = {
    "fixed_v1": fixed_windows,
    # "function_v2": tree-sitter, Phase 03 step 4.
}
