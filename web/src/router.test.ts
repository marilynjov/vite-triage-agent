import { expect, test } from 'vitest'
import { parseHash, runHash } from './router'

test('parseHash round-trips runHash', () => {
  expect(parseHash(runHash(17676, 2))).toEqual({ view: 'runs', issue: 17676, repeat: 2 })
})

test('parseHash: list, eval and triage', () => {
  expect(parseHash('#/runs')).toEqual({ view: 'runs' })
  expect(parseHash('#/eval')).toEqual({ view: 'eval' })
  expect(parseHash('')).toEqual({ view: 'triage' })
  expect(parseHash('#/')).toEqual({ view: 'triage' })
})

test.each(['#/runs/17676', '#/runs/17676/x', '#/runs/-1/0', '#/nope', '#/runs/1/2/3', '#/eval/x', '#/evals'])(
  'malformed %s falls back to triage',
  (h) => expect(parseHash(h)).toEqual({ view: 'triage' }),
)
