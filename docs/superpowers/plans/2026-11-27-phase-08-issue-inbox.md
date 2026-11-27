# Phase 08 Issue Inbox Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Let Marilyn triage her own repo's open issues on demand. She runs one command, and it shows what's new and what it would cost, then asks before re-indexing, before spending, and before posting each comment.

**Architecture:** This adds a new package, `inbox/`, that reuses the existing pieces unchanged: corpus config through `ENV_FILE`, retrieval, `agent.loop.run` and the miner for scoring. It never runs on a schedule. `python -m inbox` does five things:
1. Syncs the clone and offers to re-pin and re-index if the branch moved.
2. Lists open issues that don't yet have a triage comment.
3. Shows the cost before asking which issues to run.
4. Runs the agent on the chosen ones under a per-session cap.
5. Shows each answer and asks before posting it as a comment.

Every run is appended to `<DATA_DIR>/live/runs.jsonl`. `python -m inbox score` (free) joins those runs with the miner's labels once the issues close, which gives an accuracy number from real use.

**Tech Stack:** Python 3.12, httpx (already a dependency, used by `mining/gh.py`), the existing agent loop on Claude Haiku 5.5, git, pytest with `httpx.MockTransport`.

**Spec:** none. The design was agreed in conversation on 2026-11-27: on demand rather than scheduled, ask before re-index, spend and post, a session spending cap, comments on the repo's own issues, and live scoring. The decisions below stand in for a spec.

### Decisions

| Decision | Choice |
|---|---|
| Trigger | Manual only: `python -m inbox`. No launchd, no webhook, no flag that skips the questions. Tests inject the `ask` function; the CLI always uses `input()`. |
| Questions, in order | (1) "Branch moved `<old>` → `<new>`: re-pin and re-index? (free, about a minute) [y/N]". (2) "Triage which? [all / comma-separated numbers / none]". (3) "Spend up to $X on N issues (typical about $0.002 each)? [y/N]". (4) Per issue, after showing the answer: "Post this comment to #N? [y/N]". Every default is **No**: an empty answer or Ctrl-D does nothing. |
| Spending | `--max-dollars` per session, default **0.10**. The worst case shown is `min(N × AGENT_MAX_DOLLARS, --max-dollars)`. The session stops starting issues once its spend reaches the cap, and each run keeps its own budgets (12 turns, 150k tokens, 180 s, $0.05). |
| Which commit | The live corpus follows its default branch. When `origin/<branch>` differs from `pinned_sha.txt`, question (1) offers to write the new SHA to `pinned_sha.txt` and run `python -m retrieval.index --strategy fixed_v1_header --rebuild`. A No keeps the old pin, so answers are about older code, and the session says so. |
| Already triaged | A comment containing the marker `<!-- vite-triage-agent -->` means done. Runs that were never posted are still in `runs.jsonl` and count as done unless `--again N` is given. |
| Not-a-code-bug | The agent already returns `issue_type` and `confidence`. If `issue_type` is in `{"enhancement", "documentation", "question"}` or `confidence < 0.5`, the preview says "probably not a code fix; not recommended to post", but question (4) is still asked. |
| Comment | Markdown. The suspected files are a numbered list, linked to the file at the SHA it read, followed by the reasoning (at most 1,500 characters), the issue type, the confidence, and a line with turns, tools used and cost. It ends with the marker and "Automated suggestion; may be wrong." |
| Scoring | `python -m inbox score` runs nothing paid. It reads `runs.jsonl` and `<DATA_DIR>/dataset.jsonl`, which the user refreshes with `python -m mining all`. For each issue in both, it scores the latest run's `suspected_files` against `gold_files` with `eval.retrieve.score_ranking`. It prints n, recall@1, recall@5 and MRR, plus the issues still waiting for a fix. |
| Privacy | The target repo may be private, and this repo is public. Nothing from it goes into code, tests, docs or commits. Tests use the fake repo `acme/widgets`. All state lives under `DATA_DIR`, which for a private corpus is gitignored (`data/bowling/`). Issue text and code snippets go to Anthropic's API, as they already do in the UI. |

## Global Constraints

- Never commit anything from a private corpus: no names, issue text, paths or run records. `git status` must show nothing under `data/` before each commit.
- No new dependencies.
- All GitHub and Anthropic calls in tests are faked (`httpx.MockTransport`, an injected `run_fn`). The suite makes no network calls.
- The CLI has no non-interactive mode. Every spend and every post is confirmed by a typed `y`.
- Commits carry no Co-Authored-By or other attribution lines.
- Use `.venv/bin/python` / `.venv/bin/pytest`.
- Executing this plan spends nothing except Task 6 Step 3, which is a live session Marilyn runs herself.

## Review Focus

- An answer of `""`, `"n"`, `"N"`, `"no"`, or EOF at any question does nothing: no re-index, no spend, no post (Task 4 test).
- A session whose cap is reached mid-way: the remaining issues are skipped and listed as skipped, and nothing is posted without its own `y` (Task 4 test).
- A GitHub 401/403/404 (a token without `repo` scope, as with her `.env` token): one clear message naming `GITHUB_TOKEN=$(gh auth token)`, and no traceback (Task 1 test).
- Issue lists that include pull requests (GitHub returns PRs from `/issues`): PRs are dropped (Task 1 test).
- An agent run that fails (budget, rejected answer): it is recorded with its error and cost, nothing is offered for posting, and the session continues (Task 3 test).

---

### Task 1: GitHub client for one repo

**Files:**
- Create: `inbox/__init__.py` (empty), `inbox/github.py`, `tests/test_inbox_github.py`

**Interfaces:**
- Produces:
  - `Issue` (frozen dataclass): `number: int`, `title: str`, `body: str`, `created_at: str`, `url: str`
  - `GitHubError(Exception)`
  - `class Repo:` with `__init__(owner: str, name: str, token: str, *, transport: httpx.BaseTransport | None = None)` and these methods:
    - `open_issues() -> list[Issue]`: paginated, PRs dropped, body `None` becomes `""`, sorted by number.
    - `has_marker(number: int, marker: str) -> bool`: true if any comment contains it.
    - `post_comment(number: int, body: str) -> str`: returns the comment's `html_url`.
  - A 401, 403 or 404 raises `GitHubError("GitHub refused <path> (<status>). For a private repo use GITHUB_TOKEN=$(gh auth token), which has repo scope.")`.

- [ ] **Step 1: Write the failing tests:**
  - `test_open_issues_drops_prs_and_pages`: two pages via the `Link: rel="next"` header, one item with a `pull_request` key, and a `None` body.
  - `test_has_marker`
  - `test_post_comment_sends_the_body_and_returns_the_url`: asserts the method, path and JSON body.
  - `test_a_refusal_names_the_gh_token_fix`: parametrized over 401, 403 and 404.

  All of them build `Repo("acme", "widgets", "t", transport=httpx.MockTransport(handler))`.
- [ ] **Step 2: Run** `.venv/bin/pytest tests/test_inbox_github.py -q`. Expected: FAIL (no module).
- [ ] **Step 3: Implement.** Use the same headers as `mining/gh.py` (`Accept`, `X-GitHub-Api-Version`, `Bearer`), and follow `Link rel=next` for pagination, as the GitHub-pagination memory and `mining/gh.py` do.
- [ ] **Step 4: Run** the tests. Expected: PASS.
- [ ] **Step 5: Commit.** `git commit -m "Inbox: a small GitHub client for one repo's issues and comments"`

### Task 2: Sync the clone and re-pin on request

**Files:**
- Create: `inbox/sync.py`, `tests/test_inbox_sync.py`

**Interfaces:**
- Produces:
  - `branch_head(repo_path: Path, branch: str) -> str`: runs `git fetch origin <branch>` and returns the SHA of `origin/<branch>`.
  - `repin(sha: str, *, pin_path: Path = M.PINNED_SHA, reindex=_reindex) -> None`: writes the pin, then calls `reindex()`.
  - `_reindex()`: runs `[sys.executable, "-m", "retrieval.index", "--strategy", "fixed_v1_header", "--rebuild"]` with the current environment and `check=True`.
  - `default_branch(repo_path) -> str`: from `git symbolic-ref refs/remotes/origin/HEAD`, falling back to `main`.

- [ ] **Step 1: Write the failing tests:**
  - `test_branch_head_sees_a_new_remote_commit`: build a bare origin and a clone under `tmp_path`, commit to the origin, and check that `branch_head` on the clone returns the new SHA.
  - `test_repin_writes_the_pin_then_reindexes`: use a fake `reindex` that records the pin file's content at call time.
  - `test_default_branch_falls_back_to_main`
- [ ] **Step 2: Run** `.venv/bin/pytest tests/test_inbox_sync.py -q`. Expected: FAIL.
- [ ] **Step 3: Implement.**
- [ ] **Step 4: Run** the tests. Expected: PASS.
- [ ] **Step 5: Commit.** `git commit -m "Inbox: follow the default branch, re-pin and re-index on request"`

### Task 3: Triage one issue, render the comment, record the run

**Files:**
- Create: `inbox/triage.py`, `tests/test_inbox_triage.py`

**Interfaces:**
- Produces:
  - `MARKER = "<!-- vite-triage-agent -->"`
  - `Outcome` (dataclass): `issue: int`, `sha: str`, `files: list[str]`, `issue_type: str | None`, `confidence: float | None`, `reasoning: str`, `turns: int`, `tools: dict[str, int]`, `cost_usd: float`, `error: str | None`
  - `triage(issue: Issue, sha: str, *, search_fn=search.search_ranked, run_fn=loop.run) -> Outcome`:
    - It retrieves the top `C.GROUNDED_K` candidates for the corpus with `R.DEFAULT_STRATEGY`.
    - It runs the agent with `cutoff=issue.created_at` inside `tools.at_rev(sha)`.
    - It never raises: a `RunError` becomes `error` with its run's cost and turns.
  - `recommend(o: Outcome) -> bool`: False when `error`, when `issue_type` is in the not-a-code-bug set, or when `confidence < 0.5`.
  - `render(o: Outcome, issue: Issue, owner: str, name: str) -> str`: the comment in the Decisions table's format. File links are `https://github.com/{owner}/{name}/blob/{sha}/{path}`.
  - `record(o: Outcome, posted_url: str | None, path: Path) -> None`: appends one JSON line with every `Outcome` field plus `posted_url` and `at`, creating the directory.
  - `RUNS = M.DATA / "live" / "runs.jsonl"`

- [ ] **Step 1: Write the failing tests** (fake `search_fn` and `run_fn`; the run returns a `GroundedTriageResult`):
  - `test_triage_returns_the_agents_files_and_cost`
  - `test_triage_reads_at_the_given_sha`: inside `run_fn`, assert `tools.current_rev() == sha`.
  - `test_a_failed_run_is_an_outcome_with_its_cost`
  - `test_recommend_rules`: parametrized over error, `question`, confidence 0.4 → False, and a confident bug → True.
  - `test_render_links_files_at_the_sha_and_ends_with_the_marker`
  - `test_render_truncates_long_reasoning_to_1500`
  - `test_record_appends_one_line_per_call`
- [ ] **Step 2: Run** `.venv/bin/pytest tests/test_inbox_triage.py -q`. Expected: FAIL.
- [ ] **Step 3: Implement.**
- [ ] **Step 4: Run** the tests. Expected: PASS.
- [ ] **Step 5: Commit.** `git commit -m "Inbox: triage one issue at a chosen commit, render the comment, record the run"`

### Task 4: The interactive session

**Files:**
- Create: `inbox/__main__.py`, `inbox/session.py`, `tests/test_inbox_session.py`

**Interfaces:**
- Consumes: Tasks 1–3.
- Produces:
  - `session(*, repo: Repo, repo_path: Path, ask=input, out=print, max_dollars: float = 0.10, again: set[int] = frozenset(), triage_fn=triage, sync=(branch_head, repin), runs_path=RUNS) -> dict`. It returns `{"spent": float, "triaged": [int], "posted": [int], "skipped": [int]}`.
  - `yes(answer: str) -> bool`: true only for `y` or `yes`, case-insensitive and stripped.
  - `ask` raising `EOFError` counts as No.
  - `__main__`: `python -m inbox [--max-dollars X] [--again N ...]` and `python -m inbox score`.
    - It builds the `Repo` from `REPO_OWNER`, `REPO_NAME` and `GITHUB_TOKEN`, and uses `M.REPO_PATH`.
    - It refuses to start unless `PROVIDER=anthropic`.
    - It catches `GitHubError` and prints its message.

- [ ] **Step 1: Write the failing tests.** Each one scripts `ask` with a list of answers, uses fake `Repo`, `triage_fn` and `sync`, and asserts on calls:
  - `test_every_question_defaults_to_no`: parametrized answers `"" / "n" / "no" / EOF` at each of the four questions. Nothing re-indexes, nothing runs past a declined spend, nothing is posted.
  - `test_an_unmoved_branch_asks_nothing_about_reindexing`
  - `test_issues_with_the_marker_or_a_recorded_run_are_not_offered`, and the same issue *is* offered with `again={n}`.
  - `test_choosing_numbers_runs_only_those`
  - `test_the_cap_stops_new_runs_and_lists_the_rest_as_skipped`: fake outcomes cost $0.04 each, the cap is $0.10, and 4 issues are chosen. The result is 3 run, 1 skipped.
  - `test_a_post_needs_its_own_yes_and_unrecommended_ones_say_so`
  - `test_every_run_is_recorded_posted_or_not`
  - `test_the_cli_refuses_without_the_anthropic_provider`
- [ ] **Step 2: Run** `.venv/bin/pytest tests/test_inbox_session.py -q`. Expected: FAIL.
- [ ] **Step 3: Implement.** The spend question shows the issue count, the cap, the worst case and "typical about $0.002 each". After each run, show a compact preview in the terminal: files, type, confidence, cost, and whether it's recommended.
- [ ] **Step 4: Run** `.venv/bin/pytest -q`. Expected: the whole suite passes.
- [ ] **Step 5: Commit.** `git commit -m "Inbox: an interactive session that asks before re-indexing, spending and posting"`

### Task 5: Live score

**Files:**
- Create: `inbox/score.py`, `tests/test_inbox_score.py`

**Interfaces:**
- Produces: `score(runs: list[dict], dataset: list[dict]) -> dict` with keys `n`, `recall_at_1`, `recall_at_5`, `mrr` and `waiting: [int]`.
  - It uses the latest run per issue that has no error.
  - It scores issues present in `dataset` with `score_ranking(files, set(gold_files), [1, 5])`.
  - `waiting` lists issues that were run but aren't in the dataset yet.
- `main()` prints this and tells the user to refresh labels with `python -m mining all` first.

- [ ] **Step 1: Write the failing tests:**
  - `test_score_uses_the_latest_successful_run`
  - `test_issues_without_a_label_are_waiting`
  - `test_nothing_to_score_is_zero_not_an_error`
- [ ] **Step 2: Run** them. Expected: FAIL.
- [ ] **Step 3: Implement.**
- [ ] **Step 4: Run** them. Expected: PASS.
- [ ] **Step 5: Commit.** `git commit -m "Inbox: score live runs against the fixes once issues close"`

### Task 6: Docs and the first real session

**Files:**
- Modify: `README.md` (a new "Triage your own repo" section), `ARCHITECTURE.md` (Layout row for `inbox/`, phase table row 08)

- [ ] **Step 1: Docs.** Add the README section with the command:

  `ENV_FILE=.env.<name> GITHUB_TOKEN=$(gh auth token) PROVIDER=anthropic python -m inbox`

  Then cover the four questions, the cap, `--again`, `inbox score`, and the privacy note. Name no private repo.
- [ ] **Step 2: Run** `.venv/bin/pytest -q && npm --prefix web test`, check that `git status` shows nothing under `data/`, then commit with `git commit -m "Inbox: docs"`.
- [ ] **Step 3: STOP. Marilyn runs the first session herself** in her terminal, against her repo, with the default $0.10 cap. She answers each question. The executor does not run it. Afterwards she can share the printed summary, never the issue text, if she wants it recorded.
