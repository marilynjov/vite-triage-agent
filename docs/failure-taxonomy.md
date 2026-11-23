# Failure taxonomy

> Claude drafted these labels for Marilyn to review. The categories are provisional.

These are 20 failures of the `agent` arm on all 206 val rows (`data/runs/val-n206-r1.jsonl`, one repeat). A failure is a clean row where recall@1 is 0 and the record is not an API error; there are 67 such rows. The 20 were drawn with seed 0 and sorted by issue (`python -m eval.taxonomy sample`). Each one was read by hand: the issue text, the retrieved candidates, what the agent picked, and every tool call. The labels are in [`failure-taxonomy.jsonl`](failure-taxonomy.jsonl), one per failure, each with a sentence of evidence. Counts come from `python -m eval.taxonomy report`.

Each failure gets one category: the main reason it missed, judged from the record. Ties go to the category that points at a fix.

## Categories

| Category | n | Definition | Example |
|---|---|---|---|
| `didnt-look-past-candidates` | 6 | The gold file was not among retrieval's top 10, and the agent answered in one turn without a tool call, so it never had a chance to find it. | #17407: the gold file is `asset.ts`; it picked `build.ts` for a library-mode bug. |
| `stopped-at-the-symptom` | 5 | The agent picked the file where the problem shows: where the stack trace ends, the file that writes the wrong output, or the file the reporter links. The fix was elsewhere. | #18527: the stack trace ends in `openBrowser.ts`; the fix was in the watcher and server. |
| `right-file-not-first` | 4 | The agent named the gold file, often after reading it, but ranked another file above it. | #18244: it read `injectQuery` in `utils.ts` (gold) and still ranked `client.ts` first. |
| `no-answer` | 2 | No scorable file: an empty list, or an answer the scope check rejected. | #17508: it named `packages/vite/types/customEvent.d.ts`, which is outside the scored scope. |
| `vague-report` | 2 | The report gives no code-level clue (no error, no API name, no config), so no file is findable from the text. | #15284: a screenshot, a repo link and a debug log. |
| `other` | 1 | It fits none of the above. | #18489: a feature request whose fix touches 7 files. |

```
20 labelled failures
  didnt-look-past-candidates    6
  stopped-at-the-symptom        5
  right-file-not-first          4
  no-answer                     2
  vague-report                  2
  other                         1
```

## Across categories

- **The gold file was outside the candidates in 10 of 20.** For 7 of those 10 the agent made no tool call. In the other 3 it searched: #15022 and #18244 found the gold file but ranked it lower, and #14009 stopped at the crash site.
- **13 of 20 made no tool call at all.** The agent can search outside the candidates, but in most misses it doesn't try.

## What to fix first

`didnt-look-past-candidates` is the largest category and the cheapest to act on. If the agent searched whenever none of the candidates clearly explains the report, it would at least have a chance at these 6. Phase 05a measured a "look first" nudge that made clean recall@1 no better and all-row recall@1 worse, so a blanket instruction isn't the fix. A conditional one is the next thing to try, and it is a 07b experiment because it costs credit. `stopped-at-the-symptom` is next: for a stack trace or a linked file, the prompt could ask where the bad value came from, not only where it was seen.

## Caveats

- 20 of 67 failures, one repeat, one labeller. Phase 04 showed the agent is nondeterministic per run, so a second repeat of the same issue could fail differently.
- The tools read code at the pin, after every val fix (see `ARCHITECTURE.md` section 7). A file the agent read may already contain the fix.
