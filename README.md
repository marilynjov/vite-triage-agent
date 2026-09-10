# Vite Triage Agent

An agent that reads a GitHub issue, investigates the Vite codebase, and proposes
where the fix goes — graded against what the real fix actually touched.

**Roadmap:** https://claude.ai/code/artifact/e3009790-bc86-4b92-bc51-628065760b54

---

## Phase 00 — Ground truth first

No model code in this phase. It exists to answer one question before anything
is built on top of it:

> Does this repo yield enough labeled issue → fix examples to measure with?

If the answer is no, we find out in two days instead of six weeks.

### The chain being reconstructed

Vite squash-merges, so a commit subject looks like `fix(css): ... (#12345)`
where `12345` is the **pull request**, not the issue. The link back to the
issue lives in the PR body (`fixes #12300`). So:

```
issue  --(closing keyword in PR body)-->  PR  --(#ref in commit subject)-->  commit  -->  files
```

Plus a shortcut for commits whose own message closes an issue directly.

### Setup

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt

cp .env.example .env      # then paste a GitHub token into it
git clone https://github.com/vitejs/vite.git repos/vite
```

A token is not optional in practice: without one the API allows 60 requests
per hour, with one it allows 5000.

### Run

```bash
python -m mining all       # commits -> fetch -> build -> split
python -m mining inspect   # then read ten of them by hand
```

Or one stage at a time — each writes a file the next one reads, so re-running
a later stage is free.

| Stage | Network | Writes |
|---|---|---|
| `commits` | no | `data/raw/commits.jsonl`, `data/pinned_sha.txt` |
| `fetch` | yes | `data/raw/issues.jsonl`, `data/raw/pulls.jsonl` |
| `build` | no | `data/dataset.jsonl` + the yield report |
| `split` | no | `data/splits/{train,val,test}.jsonl` |

### The gate

`build` prints a funnel and a verdict. **≥300 usable examples** and Vite is the
corpus. Between 150 and 300, widen `SINCE_YEARS` or `MAX_GOLD_FILES` first.
Below 150, re-point at TanStack Query and then date-fns by editing `.env` —
nothing in the pipeline is Vite-specific.

### Decisions worth knowing about

**Pinned SHA.** The whole project is evaluated against one commit of Vite.
A 2022 fix may have touched a file that no longer exists today; that label is
unretrievable, so those rows are dropped rather than counted as failures.

**Scope.** Only `packages/vite/src/**/*.ts` counts as a gold label. Docs,
playground and test files are excluded — a retrieval system that surfaces a
changelog entry has not found the bug.

**Leakage flag.** Some issues literally name the file that turned out to be
broken. Finding it is then string matching, not retrieval. Those rows are kept
but flagged `mentions_gold_path`, and the report shows the rate — score with
and without them, and quote the harder number.

**Temporal split.** Sorted by creation date, not shuffled. Train is the past,
test is the future. A random split would let the system learn from issues
filed after the ones it is tested on, which never happens in production.

**test.jsonl is sealed until Phase 07.** Tune against `val`.
