"""python -m mining <stage>"""
from __future__ import annotations

import sys

STAGES = """
  commits   read the local clone, pin a SHA, extract commits + files   (offline)
  fetch     download closed issues and PRs from the GitHub API         (network)
  build     join them into dataset.jsonl and print the yield report
  split     cut into train / val / test by creation date
  inspect   print 10 random examples for a manual read
  all       commits -> fetch -> build -> split
"""


def main() -> None:
    stage = sys.argv[1] if len(sys.argv) > 1 else ""

    if stage in ("commits", "all"):
        from .gitlog import harvest
        harvest()
    if stage in ("fetch", "all"):
        from .gh import fetch
        fetch()
    if stage in ("build", "all"):
        from .build import build
        build()
    if stage in ("split", "all"):
        from .split import split
        split()
    if stage == "inspect":
        from .inspect import inspect
        inspect(int(sys.argv[2]) if len(sys.argv) > 2 else 10)
    if stage not in ("commits", "fetch", "build", "split", "inspect", "all"):
        print(f"usage: python -m mining <stage>\n{STAGES}")
        sys.exit(1)


if __name__ == "__main__":
    main()
