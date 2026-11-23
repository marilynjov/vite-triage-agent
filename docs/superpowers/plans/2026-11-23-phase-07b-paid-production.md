# Phase 07b Paid Production Work Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Finish the roadmap: turn on and verify prompt caching, rerun grounded triage on the free local model across all of val, then open the test split exactly once and record the final numbers.

**Architecture:** Caching is a change to the request that `agent/loop.py` builds: `cache_control` on the system block and on the last message. The stored history is never touched. Ablation records gain the two cache token counts, which `api/cost.py` already prices. The local-model rerun is a measurement with existing code. Opening the test split goes through a new seal, `eval/seal.py`. It allows only the two run names this plan declares, only from a clean working tree, and records the commit each one ran at. `eval.ablation` asks the seal before it reads `test.jsonl`.

**Tech Stack:** Python 3.12, Anthropic Messages API (`claude-haiku-5-5`), Ollama (`qwen2.5-coder:7b`), pytest.

**Spec:** none. Scope comes from the roadmap's Phase 07 line ("prompt caching verified via `cache_read_input_tokens` … the test split opens here for the first and only time … cheaper-model rerun") and from the decisions below. Rulings made without a spec are provisional.

### Scope and decisions

| Decision | Choice |
|---|---|
| Budget | $5.07 of credit. The caps add up to $2.65: caching check $0.25, test arms $1.60, staged test slice $0.80. Each paid command passes `--max-dollars`, and the per-run caps stay as they are (`AGENT_MAX_DOLLARS` 0.05). |
| Caching | It is controlled by `PROMPT_CACHE` (default `1`) in `api/config.py`. When on, each request marks two cache breakpoints: the system prompt (as one text block with `cache_control: {"type": "ephemeral"}`) and the last content block of the last message. Tools come before system in the cache prefix, so the first breakpoint covers both. Breakpoints are added to a copy for the request. `out.messages` stays append-only and has no `cache_control` in it. If the cached prefix is under the model's minimum cacheable length, the API caches nothing and the measurement says so. Either result is the gate's answer. |
| Caching gate | Run the `agent` arm on the 40-row val slice, one repeat, with caching on. Compare it with the `agent` arm already in `data/runs/val-n40-r3.jsonl` (3 repeats, no caching) on $/triage, and sum `cache_read_input_tokens`. Recall is reported only as a sanity check, since caching must not change what the model sees. |
| Cheaper model | Haiku 5.5 is already the cheapest Claude model, so the cheaper rerun is the free local `qwen2.5-coder:7b` doing grounded triage on all 206 val rows. Grounded is the only model arm that runs on Ollama; tool use is Anthropic-only. Before this it had been measured on only 40 rows (9 of 40 invented paths). |
| Test split | The test split is opened only after Tasks 1–2 are committed and Marilyn says go. There are two runs, both named in `eval/seal.py`: `test-final` (arms `retrieval,grounded,agent`, every test row, 1 repeat) and `test-staged-n40` (arm `staged`, the first 40 test rows by issue number, 1 repeat). Nothing is tuned after either run. The same name may resume a crashed run; any other name is refused. |
| Not in scope | Any prompt change, including the conditional "search when the candidates look weak" idea from the failure taxonomy. Testing it means tuning on val, and doing that after test opens would make test a tuning set. If wanted, it goes before Task 4, as its own val experiment, or never. |

## Global Constraints

- The executor never prints, reads into context, or quotes the text of a test row: no issue titles, bodies or file lists. Test results are read only through `python -m eval.ablation --report PATH`, which prints aggregates.
- Nothing reads `data/splits/test.jsonl` except `eval.ablation` after `seal.open_test` has allowed it.
- Task 4 does not start without Marilyn's explicit go in this conversation, given after she has seen the Task 1–3 results.
- No new pip or npm dependencies.
- Commits carry no Co-Authored-By or other attribution lines.
- Use `.venv/bin/python` / `.venv/bin/pytest`. Paid commands need `PROVIDER=anthropic`, and Postgres plus Ollama (embeddings) must be up.

## Review Focus

- A multi-turn agent run with caching on: turn 3's request must carry exactly two breakpoints (system and the newest message). Breakpoints from turns 1 and 2 must not pile up past the API's limit of 4, which would be a 400 on a long run (Task 1 test).
- A message whose last content block is a `tool_result` list rather than a text string: the breakpoint goes on the block itself, and plain-string content is first turned into one text block (Task 1 test).
- `PROMPT_CACHE=0`: the request is byte-for-byte what it was before this phase, with no `cache_control` anywhere and `system` still a string (Task 1 test).
- A crashed `test-final` run resumed under the same name: allowed, and it keeps the original commit. A second run under a new name, or from a dirty tree: refused before `test.jsonl` is opened (Task 3 test).
- `--split test` without `--open-test`, or with `--split val --open-test`: refused with a clear message, so the flag can't open test by accident or be passed for no reason (Task 3 test).

---

### Task 1: Prompt caching in the loop, with cache tokens in run records

**Files:**
- Modify: `api/config.py` (`PROMPT_CACHE`), `agent/loop.py` (request building), `eval/ablation.py` (`make_record`)
- Test: `tests/test_loop.py`, `tests/test_ablation.py`

**Interfaces:**
- Produces: `loop._cached_request(system: str, messages: list[dict], cache: bool) -> tuple[str | list[dict], list[dict]]` returns the `system` and `messages` to send. When on, `system` is `[{"type": "text", "text": system, "cache_control": {"type": "ephemeral"}}]` and the last message is deep-copied with `cache_control` on its last content block. When off, both are returned unchanged.
- Produces: ablation records gain `cache_read_input_tokens` and `cache_creation_input_tokens`, the sums over turns.

- [ ] **Step 1: Write the failing tests:**
  - `test_cache_marks_system_and_only_the_newest_message`. Build three messages (user str, assistant blocks, user `tool_result` list). Assert:
    - `system[0]["cache_control"] == {"type": "ephemeral"}`
    - exactly one `cache_control` across all messages, on `messages[-1]["content"][-1]`
    - the input list is not mutated (deep equality with a copy taken before)
  - `test_a_string_last_message_becomes_one_text_block_with_the_breakpoint`.
  - `test_cache_off_returns_the_request_unchanged`.
  - `test_a_three_turn_run_never_sends_more_than_two_breakpoints` reuses the fake client in `tests/test_loop.py`. It records each request and counts `cache_control` occurrences per request (≤ 2), and asserts the final `out.messages` contains none.
  - In `tests/test_ablation.py`: `test_record_sums_cache_tokens`, with fake turns carrying 10/20 and 0/5 read/creation tokens, expecting 10 and 25.
- [ ] **Step 2: Run** `.venv/bin/pytest tests/test_loop.py tests/test_ablation.py -q`. Expected: FAIL (no `_cached_request`, no record keys).
- [ ] **Step 3: Implement.** Add `PROMPT_CACHE = os.getenv("PROMPT_CACHE", "1") == "1"` to `api/config.py`. The loop builds `system, messages = _cached_request(system, out.messages, C.PROMPT_CACHE)` into the request. `make_record` sums the two fields from `turns`, which `_record_turn` already fills. Add `PROMPT_CACHE` with a one-line comment to `.env.example`.
- [ ] **Step 4: Run** `.venv/bin/pytest -q`. Expected: the whole suite passes.
- [ ] **Step 5: Commit**

```bash
git add api/config.py agent/loop.py eval/ablation.py tests/test_loop.py tests/test_ablation.py .env.example
git commit -m "Prompt caching: system and newest message as breakpoints, cache tokens in run records"
```

- [ ] **Step 6: The caching gate (paid, ≤ $0.25).**

Run: `PROVIDER=anthropic .venv/bin/python -m eval.ablation --arms agent --split val --n 40 --repeats 1 --name val-n40-cache --max-dollars 0.25`, then `.venv/bin/python -m eval.ablation --report data/runs/val-n40-cache.jsonl` and `.venv/bin/python -m eval.ablation --report data/runs/val-n40-r3.jsonl`.

Then sum the two cache fields over `val-n40-cache.jsonl` with a short `python -c`. Expected: 40 records, 0 api errors. `cache_read_input_tokens` > 0 means caching engaged. Compare $/triage with `val-n40-r3`'s agent arm. If the read sum is 0, the prefix is under the minimum: record that, and set the `PROMPT_CACHE` default to `0` if creation tokens made it cost more. Ledger every outcome with its numbers.

### Task 2: Grounded triage on the local model, all of val ($0)

**Files:**
- Modify: none (measurement). The numbers go to `MEASUREMENTS.md` in Task 5.

- [ ] **Step 1: Run** `PROVIDER=ollama .venv/bin/python -m eval.grounded --split val --n 206 > .superpowers/sdd/<plan-workspace>/qwen-grounded.log 2>&1`. Ollama must be serving `qwen2.5-coder:7b` (`ollama list`). `OLLAMA_NUM_CTX` stays at its default of 16384, which ARCHITECTURE.md section 4 says a grounded prompt needs.
- [ ] **Step 2: Read the tail of the log.** Expected:
  - recall@1/@5 and MRR for all rows and clean rows
  - contract violations (invented paths)
  - wall clock

  Compare with Haiku grounded on the same 206 rows: clean recall@1 45.2% in `val-n206-r1.jsonl`, via `--report`. No commit; ledger the numbers.

### Task 3: The test-split seal

**Files:**
- Create: `eval/seal.py`, `tests/test_seal.py`
- Modify: `eval/ablation.py` (`--open-test`; replace the "refusing the test split" exit with the seal)

**Interfaces:**
- Produces:
  - `seal.TEST_RUNS = ("test-final", "test-staged-n40")`
  - `seal.SEAL = M.SPLIT_DIR / "TEST_OPENED.json"`
  - `seal.open_test(name: str, *, path: Path = SEAL, clean=_tree_is_clean, head=_head_sha, now=None) -> dict`
    - It returns the seal entry and raises `SystemExit` with a message when refused.
    - It refuses a name not in `TEST_RUNS`.
    - It refuses a first opening from a dirty tree (`git status --porcelain` non-empty, ignored files excluded).
    - It writes `{name: {"opened_at", "commit"}}` on the first opening of a name.
    - It returns the stored entry unchanged on a resume.
- `ablation.main`: `--open-test` (store_true).
  - With `--split test`, the flag is required and `seal.open_test(path.stem)` runs before `val_rows`.
  - With any other split, the flag is refused.

- [ ] **Step 1: Write the failing tests:**
  - `test_an_undeclared_name_is_refused`
  - `test_first_open_records_commit_and_time`
  - `test_a_resume_keeps_the_first_commit`: a second call with another `head` still returns the first commit.
  - `test_a_dirty_tree_is_refused_on_first_open`
  - `test_ablation_refuses_test_without_the_flag_and_the_flag_without_test`: call `ablation.main([...])` with `seal.open_test` and `val_rows` spied. Neither is reached when refused.
  - `test_ablation_asks_the_seal_before_reading_test`: in call order, `open_test` comes before `val_rows("test", ...)`. Stub `val_rows` to `[]` so nothing runs or reads the real file.

  All seal tests use `tmp_path` for `path`.
- [ ] **Step 2: Run** `.venv/bin/pytest tests/test_seal.py -q`. Expected: FAIL (module missing).
- [ ] **Step 3: Implement** `eval/seal.py` and the ablation change. Update the module docstring line "The test split is refused" in `eval/ablation.py` to describe the seal.
- [ ] **Step 4: Run** `.venv/bin/pytest -q`. Expected: the whole suite passes.
- [ ] **Step 5: Commit**

```bash
git add eval/seal.py eval/ablation.py tests/test_seal.py
git commit -m "Test-split seal: two declared runs, a clean tree, the commit recorded"
```

### Task 4: Open the test split (paid, ≤ $2.40). STOP before Step 1.

**Files:**
- Create (by the runs): `data/runs/test-final.jsonl`, `data/runs/test-staged-n40.jsonl`, `data/splits/TEST_OPENED.json`

- [ ] **Step 0: Stop and ask.** Report Tasks 1–3 (caching result, qwen numbers, seal) and wait for Marilyn's explicit go. Without it, end the run here: Tasks 1–3 stand on their own.
- [ ] **Step 1: The final run.** First check `git status --porcelain` is empty, Postgres is up and Ollama is up.

Run: `PROVIDER=anthropic .venv/bin/python -m eval.ablation --arms retrieval,grounded,agent --split test --n 100000 --repeats 1 --name test-final --open-test --max-dollars 1.60 > <workspace>/test-final.log 2>&1`
Expected: it ends with every row done, 0 api errors (or retried by resuming under the same name), and spend under $1.60. Read only the log's tail.

- [ ] **Step 2: Report.** Run `.venv/bin/python -m eval.ablation --report data/runs/test-final.jsonl > <workspace>/test-final-report.md` and read it. Expected: clean and all recall@1/@5/MRR per arm, paired agent-vs-grounded and grounded-vs-retrieval intervals, $/triage. Never print individual records.
- [ ] **Step 3: The staged slice.**

Run: `PROVIDER=anthropic .venv/bin/python -m eval.ablation --arms staged --split test --n 40 --repeats 1 --name test-staged-n40 --open-test --max-dollars 0.80 > <workspace>/test-staged.log 2>&1`, then `--report` as in Step 2. Expected: the outcome table and "Fails most by stage". If gold regions fail to load for test rows, stop. Ledger it and don't work around it on test.
- [ ] **Step 4: Commit the seal** (the run files are gitignored):

```bash
git add data/splits/TEST_OPENED.json
git commit -m "Open the test split: test-final and test-staged-n40"
```

### Task 5: Record it

**Files:**
- Modify: `MEASUREMENTS.md`, `README.md` (Where it stands: a 07 Piece B row, the headline test numbers), `ARCHITECTURE.md` (section 4 caching bullet; the phase table: 07 B done), `data/splits/DO_NOT_OPEN_TEST.md` (it now says when it was opened, by which two runs, at which commit; point to `TEST_OPENED.json`)

- [ ] **Step 1:** Add a MEASUREMENTS.md "Phase 07b" section with four parts:
  - Caching: cache read and creation tokens, $/triage cached vs uncached, the commands.
  - qwen grounded on 206 val rows vs Haiku grounded.
  - **Test, final:** each arm's table from the report, val vs test side by side for the same arms, the paired intervals, and spend.
  - The staged test slice's outcome table.

  Quote clean numbers first, as everywhere. State the caveats: one repeat, and the pin is after every fix for test as for val.
- [ ] **Step 2:** Update the README and ARCHITECTURE rows. Total spend for 07b is the sum of `cost_usd` over the three new run files.
- [ ] **Step 3: Run** `.venv/bin/pytest -q && npm --prefix web test`. Expected: both pass.
- [ ] **Step 4: Commit**

```bash
git add MEASUREMENTS.md README.md ARCHITECTURE.md data/splits/DO_NOT_OPEN_TEST.md
git commit -m "Phase 07b: caching, the local model on val, and the test split, recorded"
```
