"""p3g — dump retrieval failures in a form a human can actually read.

    python -m eval.failures --strategy fixed_v1_header --n 20 > data/failures.md

No model calls: this re-runs the existing index and formats what came back.
Free, about 40 seconds.

Formatting rules learned the hard way:

  * the report goes in a fenced code block, never a blockquote. GitHub issue
    bodies contain their own `###` markdown headings, which in a blockquote are
    indistinguishable from this document's section headings.
  * line structure is preserved verbatim. Collapsing whitespace makes a 2,000
    character bug report into an unreadable wall.
  * every number that explains a failure is computed here, not left to be
    worked out by hand — the chunk scores, the gap to rank 1, and whether the
    words that would find the gold file appear in the issue at all.

The point is not to compute anything new. It is to answer, per failure, the one
question no metric can: why didn't the right file come back?
"""
from __future__ import annotations

import argparse
import json
import re

from mining import config as M
from retrieval import search

EXPORTED = re.compile(
    r"^export\s+(?:async\s+)?(?:function|const|class|interface|type|enum)\s+(\w+)",
    re.M,
)
CAMEL = re.compile(r"(?<!^)(?=[A-Z])")

HEADING = re.compile(r"^\s{0,3}(#{1,6})\s+(.*?)\s*#*\s*$")


def as_prose(body: str) -> str:
    """Render the report as real Markdown, with its headings demoted to bold.

    A code block was the wrong fix. It made a 2,000-character bug report
    monospace and unwrapped, and threw away the formatting that makes it
    readable — lists, links, inline code, the shell block.

    The actual problem was narrower: GitHub's issue template emits `### Describe
    the bug`, which renders identically to this document's own `###` sections.
    So demote those to bold. The hierarchy then reads cleanly: `##` is a
    failure, `###` is one of our sections, **bold** is something the reporter
    wrote.

    Heading lines inside the report's own fenced blocks are left alone — a `#`
    in a shell transcript is a comment, not a heading.
    """
    lines = body.strip().splitlines()

    # Five of 206 val bodies leave a fence open — someone pastes ``` and never
    # closes it. Untreated, that unclosed fence swallows everything after the
    # report, including this document's own Cause checklist, and suppresses
    # heading demotion for the rest of the body. Close it here.
    opens = sum(1 for l in lines if l.lstrip()[:3] in ("```", "~~~"))
    if opens % 2:
        lines.append("```")

    out: list[str] = []
    in_fence = False
    fence_marker = ""

    for line in lines:
        stripped = line.lstrip()
        if stripped.startswith("```") or stripped.startswith("~~~"):
            marker = stripped[:3]
            if not in_fence:
                in_fence, fence_marker = True, marker
            elif marker == fence_marker:
                in_fence = False
            out.append(line)
            continue

        if not in_fence:
            m = HEADING.match(line)
            if m and m.group(2):
                out.append(f"**{m.group(2)}**")
                continue
        out.append(line)

    return "\n".join(out)


def fence_for(text: str) -> str:
    """A fence long enough to contain the body verbatim.

    194 of 206 val bodies contain ``` of their own — GitHub's issue template
    puts System Info in a shell fence — and the longest run in the corpus is 4
    backticks. A 3-backtick fence is terminated by the first one inside the
    body, which silently breaks every section after it. Markdown allows a fence
    of N backticks to contain runs of fewer than N, so measure and exceed.
    """
    longest = 0
    run = 0
    for ch in text:
        run = run + 1 if ch == "`" else 0
        longest = max(longest, run)
    return "`" * max(3, longest + 1)


CAUSES = [
    ("near miss (rank 11-20)", "a reranker over the top 30-50 fixes it"),
    ("literal miss", "an exact string or identifier was present and unmatched; BM25 would catch it"),
    ("symptom-vs-cause", "the report names what broke, the fix is upstream of it"),
    ("vocabulary gap", "user-facing words vs the code's internal identifiers"),
    ("thin report", "a stack trace or bare repro link; little prose to retrieve on"),
    ("unfindable", "the fix touched a file this text could not point to"),
]


def findable_terms(gold_path: str, source_hint: str = "") -> list[str]:
    """Words that, if present in the issue, would plausibly surface the gold file.

    Derived from the path — `plugins/esbuild.ts` yields "esbuild", "plugins" —
    plus camelCase split, so `importAnalysis.ts` also yields "import analysis".
    """
    stem = gold_path.split("/")[-1].rsplit(".", 1)[0]
    parent = gold_path.split("/")[-2] if "/" in gold_path else ""
    terms = {stem.lower(), parent.lower()}
    terms.add(CAMEL.sub(" ", stem).lower())
    return sorted(t for t in terms if t and len(t) > 2)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--strategy", default="fixed_v1_header")
    ap.add_argument("--split", default="val")
    ap.add_argument("--n", type=int, default=20)
    ap.add_argument("--deep", type=int, default=600,
                    help="chunks to fetch, so a gold file's true rank is known")
    args = ap.parse_args()

    rows = sorted(
        (json.loads(l) for l in open(M.SPLIT_DIR / f"{args.split}.jsonl")),
        key=lambda r: r["issue"],
    )

    found = []
    for row in rows:
        query = f"{row['title']}\n\n{row['body']}"
        chunks = search.search_chunks(query, strategy=args.strategy, k=args.deep)

        # Collapse to ranked files exactly as search_files does, so the ranks
        # here are the ranks the harness scored.
        best: dict[str, float] = {}
        for h in chunks:
            if h.path not in best or h.score > best[h.path]:
                best[h.path] = h.score
        ranked = sorted(best.items(), key=lambda kv: -kv[1])
        paths = [p for p, _ in ranked]

        if set(row["gold_files"]) & set(paths[:10]):
            continue
        found.append((row, ranked, paths, chunks))
        if len(found) >= args.n:
            break

    top_score = lambda ranked: ranked[0][1]

    print(f"# Retrieval failures\n")
    print(f"`{args.strategy}` · `{args.split}` split · {len(found)} issues where "
          f"**no gold file reached the top 10**.\n")
    print("Scanned in issue-number order until the cap, so this set is fixed and "
          "reproducible. Regenerate with:\n")
    print("```")
    print(f"python -m eval.failures --strategy {args.strategy} --n {args.n} > data/failures.md")
    print("```\n")
    print("Fill in the **Cause** at the end of each entry. The distribution of "
          "causes is the deliverable — it decides whether the remaining gap is "
          "a ranking problem (fixable in Phase 03) or a reasoning problem "
          "(Phase 04 tools).\n")

    for n, (row, ranked, paths, chunks) in enumerate(found, 1):
        body = row["body"]
        prose = len(re.sub(r"```.*?```", "", body, flags=re.S))

        print(f"\n---\n")
        print(f"## {n} · Issue #{row['issue']}\n")
        print(f"**{row['title']}**\n")
        print("| | |")
        print("|---|---|")
        print(f"| link | <https://github.com/vitejs/vite/issues/{row['issue']}> |")
        print(f"| labels | {', '.join(f'`{l}`' for l in row['labels']) or '—'} |")
        print(f"| body | {len(body):,} chars, {prose:,} outside code fences |")
        print(f"| names a gold file in the text | {'yes' if row['mentions_gold_path'] else 'no'} |")

        print(f"\n### Gold files — and where they actually ranked\n")
        print("| file | file rank | best chunk score | gap to rank 1 |")
        print("|---|---|---|---|")
        for g in row["gold_files"]:
            rank = paths.index(g) + 1 if g in paths else None
            score = dict(ranked).get(g)
            rank_s = f"**{rank}**" if rank else f"not in top {len(paths)}"
            score_s = f"{score:.4f}" if score else "—"
            gap_s = f"{top_score(ranked) - score:.4f}" if score else "—"
            print(f"| `{g}` | {rank_s} | {score_s} | {gap_s} |")

        print(f"\n### What came back instead\n")
        print("| # | file | score |")
        print("|---|---|---|")
        for i, (path, score) in enumerate(ranked[:10], 1):
            print(f"| {i} | `{path}` | {score:.4f} |")

        print(f"\n### Would the issue's own words find it?\n")
        print("| term from the gold path | appears in the issue? |")
        print("|---|---|")
        low = f"{row['title']} {body}".lower()
        for g in row["gold_files"]:
            for term in findable_terms(g):
                print(f"| `{term}` | {'**yes**' if term in low else 'no'} |")

        print(f"\n### The report\n")
        print(as_prose(body))

        print(f"\n### Cause\n")
        for label, hint in CAUSES:
            print(f"- [ ] **{label}** — {hint}")
        print(f"\n> _notes:_\n")


if __name__ == "__main__":
    main()
