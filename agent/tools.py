"""The agent's four read-only tools, all answered from git at the pinned SHA.

Every tool reads the object database (`git show`, `git grep <sha>`,
`git ls-tree`) rather than the working tree, so what the model sees is exactly
the commit the retrieval index and the gold labels were built from, whatever
the clone has checked out. Paths are validated before they reach git, and git
is only ever invoked with fixed subcommands and a `--` before user paths, so a
model-supplied string cannot become an option or escape the repo.

The one tool that could leak the answer is `git_log`: the fix for an issue is
in the history. It takes a `cutoff` (the issue's creation time) that the
caller supplies and the model never sees, and hides every commit after it.

Failures come back as strings starting "Error: " (the model can read and
recover from them); `execute` flags them so the loop can set `is_error`.
"""
from __future__ import annotations

import posixpath
import subprocess
from functools import lru_cache

import mining.config as M
from api.schema import GroundedTriageResult

MAX_LINES = 400
MAX_MATCHES = 50
MAX_CHARS = 20_000
MAX_LOG = 30
GIT_TIMEOUT = 30


@lru_cache(maxsize=1)
def pinned_sha() -> str:
    return M.PINNED_SHA.read_text().strip()


def _git(*args: str) -> subprocess.CompletedProcess:
    # check=False: a nonzero exit is information (no match, bad path), not a crash.
    # --literal-pathspecs: a model-supplied path is a path, never `:(top)` magic or a glob.
    # The timeout keeps a pathological regex from hanging a run; execute() reports it.
    return subprocess.run(
        ["git", "--literal-pathspecs", "-C", str(M.REPO_PATH), *args],
        capture_output=True, text=True, errors="replace", check=False,
        timeout=GIT_TIMEOUT,
    )


def _split_lines(text: str) -> list[str]:
    """Split on \\n only, as `git grep -n` counts, so grep hits and read_file agree."""
    lines = text.split("\n")
    if lines and lines[-1] == "":
        lines.pop()
    return lines


@lru_cache(maxsize=1)
def pinned_date() -> str:
    return _git("log", "-1", "--format=%cI", pinned_sha()).stdout.strip()


def _safe(path: str) -> str | None:
    """Normalised repo-relative path, or None if it could escape or act as an option."""
    path = posixpath.normpath(path.strip() or ".")
    if path.startswith(("/", "-")) or ".." in path.split("/"):
        return None
    return "" if path == "." else path


def _kind(path: str) -> str | None:
    """'blob', 'tree', or None when the path does not exist at the pin."""
    r = _git("cat-file", "-t", f"{pinned_sha()}:{path}")
    return r.stdout.strip() if r.returncode == 0 else None


def list_dir(path: str = "") -> str:
    p = _safe(path)
    if p is None:
        return f"Error: unsafe path {path!r}"
    kind = _kind(p)
    if kind is None:
        return f"Error: no such path {p!r}"
    if kind != "tree":
        return f"Error: {p!r} is a file, not a directory"
    out = _git("ls-tree", f"{pinned_sha()}:{p}").stdout
    prefix = f"{p}/" if p else ""
    entries = []
    for line in out.splitlines():
        meta, _, name = line.partition("\t")
        entries.append(f"{prefix}{name}{'/' if meta.split()[1] == 'tree' else ''}")
    return "\n".join(entries)


def read_file(path: str, start_line: int = 1, end_line: int | None = None) -> str:
    p = _safe(path)
    if p is None or p == "":
        return f"Error: unsafe path {path!r}"
    kind = _kind(p)
    if kind is None:
        return f"Error: no such file {p!r}"
    if kind != "blob":
        return f"Error: {p!r} is a directory; use list_dir"
    r = _git("show", f"{pinned_sha()}:{p}")
    if "\x00" in r.stdout:
        return f"Error: {p!r} is a binary file"
    lines = _split_lines(r.stdout)
    total = len(lines)
    if start_line < 1:
        return f"Error: start_line must be >= 1, got {start_line}"
    if start_line > total:
        return f"Error: start_line {start_line} is past the end; {p} has {total} lines"
    end = min(end_line if end_line is not None else total, total, start_line + MAX_LINES - 1)
    if end < start_line:
        return f"Error: end_line {end_line} is before start_line {start_line}"
    body = "\n".join(f"{n}: {lines[n - 1]}" for n in range(start_line, end + 1))
    return f"{p}: lines {start_line}-{end} of {total}\n{body}"


def grep(pattern: str, path: str = "") -> str:
    p = _safe(path)
    if p is None:
        return f"Error: unsafe path {path!r}"
    sha = pinned_sha()
    # -e makes a pattern like "--output=x" a pattern, not an option.
    args = ["grep", "-n", "-I", "-E", "-e", pattern, sha]
    if p:
        args += ["--", p]
    r = _git(*args)
    if r.returncode == 1:
        return "showing 0 of 0 matches"
    if r.returncode != 0:
        return f"Error: grep failed: {r.stderr.strip() or 'invalid pattern'}"
    matches = [line.removeprefix(f"{sha}:") for line in r.stdout.splitlines()]
    shown = matches[:MAX_MATCHES]
    return "\n".join([f"showing {len(shown)} of {len(matches)} matches", *shown])


def git_log(path: str = "", n: int = 10, *, cutoff: str) -> str:
    p = _safe(path)
    if p is None:
        return f"Error: unsafe path {path!r}"
    n = max(1, min(int(n), MAX_LOG))
    args = ["log", pinned_sha(), f"--before={cutoff}", f"-n{n}",
            "--format=%h %cI %s", "--name-only"]
    if p:
        args += ["--", p]
    r = _git(*args)
    if r.returncode != 0:
        return f"Error: git log failed: {r.stderr.strip()}"
    return r.stdout.strip() or "No commits found before the issue was filed."


def _nullable(t: str, desc: str) -> dict:
    return {"type": [t, "null"], "description": desc}


def _spec(name: str, description: str, properties: dict) -> dict:
    return {
        "name": name, "description": description, "strict": True,
        "input_schema": {
            "type": "object", "properties": properties,
            "required": list(properties), "additionalProperties": False,
        },
    }


_PATH = "Repo-relative path; null or empty for the repo root."

TOOL_SPECS: list[dict] = [
    _spec("list_dir", "List a directory of the repository. Directories end with '/'.",
          {"path": _nullable("string", _PATH)}),
    _spec("read_file",
          f"Read a file with line numbers, at most {MAX_LINES} lines per call. "
          "The header reports the file's total length.",
          {"path": {"type": "string", "description": "Repo-relative file path."},
           "start_line": _nullable("integer", "First line, 1-based; null for 1."),
           "end_line": _nullable("integer", "Last line; null for as many as allowed.")}),
    _spec("grep",
          f"Regex (extended) search; shows up to {MAX_MATCHES} matches as path:line:text.",
          {"pattern": {"type": "string", "description": "Extended regular expression."},
           "path": _nullable("string", "Directory or file to search; null for everything.")}),
    _spec("git_log",
          f"Recent commits (hash, date, subject, files touched), newest first, up to {MAX_LOG}.",
          {"path": _nullable("string", "Limit to commits touching this path; null for all."),
           "n": _nullable("integer", "How many commits; null for 10.")}),
]


def execute(name: str, tool_input: dict, *, cutoff: str) -> tuple[str, bool]:
    """Run one tool call. Never raises: whatever goes wrong becomes an Error string."""
    try:
        if name == "list_dir":
            out = list_dir(tool_input.get("path") or "")
        elif name == "read_file":
            out = read_file(tool_input["path"], tool_input.get("start_line") or 1,
                            tool_input.get("end_line"))
        elif name == "grep":
            out = grep(tool_input["pattern"], tool_input.get("path") or "")
        elif name == "git_log":
            out = git_log(tool_input.get("path") or "", tool_input.get("n") or 10,
                          cutoff=cutoff)
        else:
            out = f"Error: unknown tool {name!r}"
    except subprocess.TimeoutExpired:
        out = f"Error: git timed out after {GIT_TIMEOUT}s"
    except Exception as e:  # a tool failure must reach the model, not kill the loop
        out = f"Error: {type(e).__name__}: {e}"
    if len(out) > MAX_CHARS:
        out = out[:MAX_CHARS] + "\n… [truncated]"
    return out, out.startswith("Error: ")


@lru_cache(maxsize=1)
def _tracked() -> frozenset[str]:
    return frozenset(_git("ls-tree", "-r", "--name-only", pinned_sha()).stdout.splitlines())


def check_in_scope(result: GroundedTriageResult) -> None:
    """The model may only name real source files at the pin, like the gold labels."""
    tracked = _tracked()
    bad = [f for f in result.suspected_files
           if f not in tracked or not M.is_gold_candidate(f)]
    if bad:
        raise ValueError(f"suspected_files not in scope at the pinned tree: {', '.join(bad)}")
