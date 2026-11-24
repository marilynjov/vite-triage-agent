# test.jsonl was opened once, in Phase 07b

Every time you look at these examples you spend a little of their value as
evidence, so the split stayed sealed while everything was tuned on `val`.

It was opened on 2026-11-24 (UTC) by the two runs `eval/seal.py` declares,
`test-final` and `test-staged-n40`; `TEST_OPENED.json` records when and at
which commit. Those are the held-out numbers (MEASUREMENTS.md, Phase 07b).
Nothing may be tuned against test now: any further change is measured on
`val`, and a new test number would no longer be held out.
