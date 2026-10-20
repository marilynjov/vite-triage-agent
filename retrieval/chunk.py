"""Chunking. v1 is fixed windows; v2 will split on syntax.

Both strategies return the same shape, so the indexer and the harness never
learn which one produced a chunk. That is what makes the v1-vs-v2 comparison a
config change rather than a rewrite.
"""
from __future__ import annotations

import re
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


IMPORT_LINE = re.compile(r"^\s*(?:import\b|export\s+(?:\*|\{)|from\b)")


def _header(path: str, source: str) -> str:
    """What a chunk should have known about itself all along.

    The path first, because `packages/vite/src/node/plugins/css.ts` is an
    enormous lexical and semantic signal that no fixed window carries. Then the
    import block, which names the modules this file collaborates with — the
    cheapest available summary of what a file is *for*.

    Capped at 20 import lines: a file that imports forty things would otherwise
    spend most of every chunk repeating them.
    """
    imports = [
        line for line in source.splitlines()[:120] if IMPORT_LINE.match(line)
    ][:20]
    parts = [path]
    if imports:
        parts.append("\n".join(imports))
    return "\n\n".join(parts) + "\n\n"


def fixed_windows_with_header(path: str, source: str) -> list[Chunk]:
    """v1 plus a header on every chunk. One variable changed, deliberately.

    This exists to separate two things `p3d` bundles together: splitting on
    syntax, and telling a chunk where it came from. If this alone recovers most
    of v2's gain, that is a more useful finding than "tree-sitter helped",
    because it says the problem was missing context rather than bad boundaries.

    Line numbers describe the *source* window, not the header, so a hit still
    points at real lines in the real file.
    """
    header = _header(path, source)
    return [
        Chunk(path=c.path, start_line=c.start_line, end_line=c.end_line,
              text=header + c.text)
        for c in fixed_windows(path, source)
    ]


STRATEGIES = {
    "fixed_v1": fixed_windows,
    "fixed_v1_header": fixed_windows_with_header,
    # "function_v2": tree-sitter, Phase 03 step 4.
}
