# Vite Triage Agent

An agent that reads a GitHub issue, investigates the Vite codebase, and proposes
where the fix goes, graded against what the real fix actually touched.

The point is that it is **measurable**. Every closed Vite issue with a linked
fix becomes a labeled example for free: the issue text is the input, and the
files the real fix changed are the answer. So every change to the system has a
number, mainly recall@k: of the files the fix touched, what fraction appear in
the top k the system proposed?

**Roadmap:** https://claude.ai/code/artifact/e3009790-bc86-4b92-bc51-628065760b54

## Where it stands

| Phase | What it built | Headline (Vite val, clean rows) |
|---|---|---|
| 00 Ground truth | the miner: issue → fix → files, a pinned commit, a temporal split | 1,375 labeled examples |
| 01 First call | `/triage/naive`, every call costed in Postgres | ~$0.003 per triage on Haiku 4.5 |
| 02 Structured output | a typed `TriageResult`, TypeScript generated from it | component accuracy 93% (Haiku) vs 53% (7B) |
| 03 Retrieval | chunking, embeddings in pgvector, the recall@k harness | recall@10 64.1% → 68.5% from a path + imports header |
| 03→04 Grounded triage | the model re-ranks retrieval's top 10 | recall@1 24.1% → 44.5% (206 rows) |
| 04 Tools | four git tools at the pinned commit, a hand-written tool-use loop, `/triage/agent`, a trace in the UI | recall@1 41.5% vs grounded 30.4% (40 rows), but 26/40 runs used no tools |
| 05 The agent loop | in progress: first, [does looking pay?](docs/superpowers/specs/2026-11-01-phase-05a-does-looking-pay-design.md) | clean recall@1 on 206 rows: 24.1% retrieval, 45.2% grounded, 53.1% agent (agent vs grounded paired interval excludes 0), $0.0031 vs $0.0018 per triage |

Every number, with its caveats, is in [MEASUREMENTS.md](MEASUREMENTS.md). How
the pieces fit and why each boundary is where it is: [ARCHITECTURE.md](ARCHITECTURE.md).

## Quick start

You need Python 3.12, Node, Docker, and [Ollama](https://ollama.com) (retrieval
embeds locally with `nomic-embed-text`, so it runs even when the model calls go
to Anthropic).

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env               # add a GitHub token; an Anthropic key for paid runs
git clone https://github.com/vitejs/vite.git repos/vite
ollama pull nomic-embed-text       # embeddings; also qwen2.5-coder:7b for free local triage
docker compose up -d               # Postgres + pgvector on port 5433

python -m mining all               # labeled dataset, pinned commit, splits (see below)
python -m retrieval.index --strategy fixed_v1_header
```

Run the app:

```bash
PROVIDER=anthropic python -m uvicorn api.main:app --port 8000 --reload
cd web && npm install && npm run dev            # http://localhost:5173
```

The page has three buttons. **Prose (Phase 01)** and **Typed triage** see only
the issue. **Agent (Phase 04)** also gets retrieval's top 10 files and four read-only tools, and shows each
tool call it made. `/triage/agent` needs `PROVIDER=anthropic`; the other
routes also run on the free local model (`PROVIDER=ollama`).

## Measuring

All evals use the fixed val slice (lowest issue numbers first), so runs are
comparable. The test split stays sealed until Phase 07.

| Command | What it scores | Cost |
|---|---|---|
| `python -m eval.retrieve --split val --n 206 --strategy fixed_v1_header` | retrieval alone: recall@1/5/10, MRR | free |
| `python -m eval.classify --n 40` | Phase 02 issue type, severity, components | free locally |
| `PROVIDER=anthropic python -m eval.grounded --n 40` | the model re-ranking retrieval's top 10 | ~$0.07 |
| `PROVIDER=anthropic python -m eval.agent --n 40` | retrieval vs grounded vs the tool agent | ~$0.18 |
| `PROVIDER=anthropic python -m eval.ablation --arms retrieval,grounded,agent-no-tools,agent,agent-look --n 40 --repeats 3` | the Phase 05a arms (retrieval, grounded, agent-no-tools, agent, agent-look) on the same rows, written to `data/runs/` | ~$1.20 |
| `python -m eval.ablation --report PATH` | scores, paired intervals, cost and tool use from a run file | free |
| `PROVIDER=anthropic python -m agent.explain <issue#>` | one run's message array, turn by turn | ~$0.01 |
| `python -m pytest -q` | the test suite; no paid calls | free |

## Another repo

Nothing is Vite-specific except the defaults. Put a second corpus's settings
(`REPO_OWNER`, `REPO_NAME`, `REPO_PATH`, `SCOPE_PREFIX`, `SOURCE_EXTS`,
`DATA_DIR`, …) in its own file, and prefix any command with `ENV_FILE`:

```bash
ENV_FILE=.env.<name> python -m mining all
ENV_FILE=.env.<name> python -m retrieval.index --strategy fixed_v1_header
ENV_FILE=.env.<name> PROVIDER=anthropic python -m uvicorn api.main:app --port 8000
```

The corpus file wins; anything it leaves out (the API key, the provider) comes
from `.env`. Keep a private repo's env file and mined data out of git: this
repo is public.

---

## Mining the ground truth (Phase 00)

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
