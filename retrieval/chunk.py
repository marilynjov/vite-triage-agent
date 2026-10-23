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


# Deliberately cross-language: `import` (JS/TS/Python/Java/Swift/Go),
# `from` (Python/JS), `use` (Rust/PHP), `require` (Ruby/JS), `#include` (C/C++),
# `package` (Java/Go). A header that silently loses its imports on a non-TS repo
# would quietly cost the +8 points the header is worth.
IMPORT_LINE = re.compile(
    r"^\s*(?:import\b|from\b|use\b|require\b|#include\b|package\b"
    r"|export\s+(?:\*|\{))"
)

# Node types that count as one retrievable unit, per tree-sitter grammar. The
# fallback heuristic covers grammars not listed: almost every tree-sitter
# language names its top-level nodes *_declaration or *_definition.
UNIT_TYPES_BY_LANGUAGE = {
    "typescript": {
        "function_declaration", "generator_function_declaration",
        "class_declaration", "interface_declaration", "type_alias_declaration",
        "enum_declaration", "lexical_declaration", "variable_declaration",
    },
    "tsx": {
        "function_declaration", "class_declaration", "interface_declaration",
        "type_alias_declaration", "enum_declaration", "lexical_declaration",
    },
    "python": {"function_definition", "class_definition", "decorated_definition"},
    "swift": {
        "function_declaration", "class_declaration", "protocol_declaration",
        "property_declaration", "typealias_declaration", "enum_declaration",
    },
    "go": {"function_declaration", "method_declaration", "type_declaration"},
    "rust": {"function_item", "struct_item", "enum_item", "impl_item", "trait_item"},
}

# importlib path per grammar: (module, factory function).
GRAMMARS = {
    "typescript": ("tree_sitter_typescript", "language_typescript"),
    "tsx": ("tree_sitter_typescript", "language_tsx"),
    "python": ("tree_sitter_python", "language"),
    "swift": ("tree_sitter_swift", "language"),
    "go": ("tree_sitter_go", "language"),
    "rust": ("tree_sitter_rust", "language"),
}


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


# Declarations worth treating as one retrievable unit. `export_statement` is
# unwrapped rather than listed, since `export function foo()` parses as an export
# wrapping the declaration.
_parser: object | None = None
_parser_for: str | None = None


def _unit_types() -> set[str]:
    return UNIT_TYPES_BY_LANGUAGE.get(R.TREE_SITTER_LANGUAGE, set())


def _is_unit(node_type: str) -> bool:
    """Known grammar: an explicit set. Unknown grammar: a suffix heuristic."""
    known = _unit_types()
    if known:
        return node_type in known
    return node_type.endswith(("_declaration", "_definition", "_item"))


def _get_parser():
    """Built once per language. Returns None when the grammar is unavailable.

    A missing grammar is not an error — it means v2 degrades to v1+header for
    that corpus, which is a working strategy, just a coarser one.
    """
    global _parser, _parser_for
    language = R.TREE_SITTER_LANGUAGE
    if not language:
        return None
    if _parser is not None and _parser_for == language:
        return _parser

    spec = GRAMMARS.get(language)
    if spec is None:
        return None
    try:
        import importlib
        import tree_sitter
        module = importlib.import_module(spec[0])
        _parser = tree_sitter.Parser(
            tree_sitter.Language(getattr(module, spec[1])())
        )
        _parser_for = language
        return _parser
    except Exception:
        # Grammar not installed for this language. Caller falls back.
        return None


def _units(node):
    """Top-level declarations, with export/decorator wrappers unwrapped."""
    for child in node.children:
        if child.type in ("export_statement", "decorated_definition"):
            inner = [c for c in child.children if _is_unit(c.type)]
            # `export { a, b }` has no declaration child — nothing to chunk.
            yield from (inner or [])
        elif _is_unit(child.type):
            yield child


def function_boundaries(path: str, source: str) -> list[Chunk]:
    """v2: one chunk per declaration, with the same header as v1+header.

    The header is kept deliberately. v1+header already measured what context is
    worth (+8.1 points of recall@1), so changing boundaries AND context at once
    would make this result unattributable. Only the boundaries move here.

    Three things this has to handle that a character window does not:

      * oversized units — some Vite functions run 400 lines, and embedding one
        as a single vector averages it into mush, so they are sub-windowed
      * methods — a 1,000-line class is not one idea; its methods are emitted
        separately when the class is too big to be a unit
      * the gaps — top-level code between declarations is still indexed, because
        a module-level constant can be exactly what a bug is about

    Falls back to v1+header for a file tree-sitter cannot parse, rather than
    silently dropping it from the index.
    """
    parser = _get_parser()
    if parser is None:
        # No grammar for this language — v1+header is the honest fallback.
        return fixed_windows_with_header(path, source)
    try:
        tree = parser.parse(source.encode("utf8"))
    except Exception:
        return fixed_windows_with_header(path, source)

    header = _header(path, source)
    raw = source.encode("utf8")

    def span(node) -> tuple[str, int, int]:
        return (
            raw[node.start_byte : node.end_byte].decode("utf8", "replace"),
            node.start_point[0] + 1,
            node.end_point[0] + 1,
        )

    pieces: list[tuple[str, int, int]] = []
    for unit in _units(tree.root_node):
        text, first, last = span(unit)

        if len(text) <= R.MAX_UNIT_CHARS:
            pieces.append((text, first, last))
            continue

        # Too big. A class splits into methods; anything else is windowed.
        body = next((c for c in unit.children if c.type == "class_body"), None)
        methods = [c for c in body.children if c.type == "method_definition"] if body else []
        if methods:
            pieces.extend(span(m) for m in methods)
        else:
            for sub in fixed_windows(path, text):
                pieces.append((sub.text, first + sub.start_line - 1,
                               first + sub.end_line - 1))

    if not pieces:
        # A file of pure re-exports or declarations tree-sitter did not classify.
        return fixed_windows_with_header(path, source)

    return [
        Chunk(path=path, start_line=first, end_line=last, text=header + text)
        for text, first, last in pieces
        if text.strip()
    ]


STRATEGIES = {
    "fixed_v1": fixed_windows,
    "fixed_v1_header": fixed_windows_with_header,
    "function_v2": function_boundaries,
}
