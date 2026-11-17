import { expect, test } from 'vitest'
import { filterRuns } from './runFilter'
import type { StagedRunSummary } from './types.generated'

const r = (issue: number, outcome: string, patch_outcome: string | null): StagedRunSummary => ({
  issue,
  repeat: 0,
  title: 't',
  outcome,
  failed_stage: null,
  cost_usd: 0,
  patch_outcome,
})
const RUNS = [r(1, 'hit', 'pass'), r(2, 'hit', null), r(3, 'locate_miss', 'test_fail')]

test('filterRuns: any outcome, all', () =>
  expect(filterRuns(RUNS, { outcome: '', patchedOnly: false })).toHaveLength(3))

test('filterRuns: by outcome', () =>
  expect(filterRuns(RUNS, { outcome: 'hit', patchedOnly: false }).map((x) => x.issue)).toEqual([1, 2]))

test('filterRuns: patched only', () =>
  expect(filterRuns(RUNS, { outcome: '', patchedOnly: true }).map((x) => x.issue)).toEqual([1, 3]))
