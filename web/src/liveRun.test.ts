import { expect, test } from 'vitest'
import { initialLiveState, isTerminal, reduce } from './liveRun'
import type { LiveState } from './liveRun'
import type { Done, Failed, LiveEvent, RunStarted, StepStarted, ToolCalled, ToolReturned, Totals, Usage } from './types.generated'

const totals: Totals = { turns: 1, tokens: 100, cost_usd: 0.01, seconds: 2 }

const runStarted: RunStarted = {
  type: 'run_started',
  seq: 1,
  candidates: [{ path: 'a.ts', score: 0.9, start_line: 1, end_line: 5 }],
}
const step: StepStarted = { type: 'step_started', seq: 2, turn: 1 }
const called: ToolCalled = { type: 'tool_called', seq: 3, turn: 1, tool_use_id: 't1', name: 'read_file', input: { path: 'a.ts' } }
const returned: ToolReturned = {
  type: 'tool_returned', seq: 4, turn: 1, tool_use_id: 't1', name: 'read_file',
  output_preview: 'hello', is_error: false, duration_ms: 12,
}
const usage: Usage = { type: 'usage', seq: 5, turn: 1, input_tokens: 60, output_tokens: 40, cost_usd: 0.01, totals }
const done: Done = {
  type: 'done', seq: 6, totals,
  result: {
    reasoning: 'r', issue_type: 'bug', severity: 'low', suspected_components: [],
    reproduction_steps: [], confidence: 'high', tool_calls: [],
  } as unknown as Done['result'],
}
const failed: Failed = { type: 'failed', seq: 6, reason: 'budget', message: 'm', totals, limit: 'tokens', value: 123 }

const sequence: LiveEvent[] = [runStarted, step, called, returned, usage, done]

function run(events: LiveEvent[]): LiveState {
  let s = reduce(initialLiveState, { kind: 'start', runId: 'r1' })
  for (const event of events) s = reduce(s, { kind: 'event', event })
  return s
}

test('applies events in order', () => {
  const s = run([runStarted, step, called])
  expect(s.lastSeq).toBe(3)
  expect(s.candidates).toEqual(runStarted.candidates)
  expect(s.calls).toHaveLength(1)
  expect(s.calls[0]).toEqual({
    toolUseId: 't1', turn: 1, name: 'read_file', input: { path: 'a.ts' },
    output: null, isError: null, durationMs: null,
  })
  expect(s.status).toBe('running')
})

test('buffers an early event and drains it when the gap fills', () => {
  const ordered = run([runStarted, step, called])
  const early = run([runStarted, called, step])
  expect(early).toEqual(ordered)
  const mid = run([runStarted, called])
  expect(mid.lastSeq).toBe(1)
  expect(Object.keys(mid.pending)).toEqual(['3'])
})

test('drops a duplicate after a reconnect', () => {
  expect(run([runStarted, step, step, called])).toEqual(run([runStarted, step, called]))
})

test('tool_returned fills the matching call by tool_use_id', () => {
  const other: ToolCalled = { ...called, seq: 3, tool_use_id: 't0' }
  const second: ToolCalled = { ...called, seq: 4, tool_use_id: 't1' }
  const ret: ToolReturned = { ...returned, seq: 5 }
  const s = run([runStarted, step, other, second, ret])
  expect(s.calls[0].output).toBeNull()
  expect(s.calls[1]).toMatchObject({ output: 'hello', isError: false, durationMs: 12 })
})

test('usage sets totals', () => {
  expect(run([runStarted, step, called, returned, usage]).totals).toEqual(totals)
})

test('connection_lost then connection_open toggles reconnecting', () => {
  let s = run([runStarted])
  s = reduce(s, { kind: 'connection_lost' })
  expect(s.status).toBe('reconnecting')
  s = reduce(s, { kind: 'connection_open' })
  expect(s.status).toBe('running')
  const finished = run(sequence)
  expect(reduce(finished, { kind: 'connection_lost' })).toBe(finished)
  expect(reduce(finished, { kind: 'connection_open' }).status).toBe('done')
  const idle = reduce(initialLiveState, { kind: 'connection_lost' })
  expect(idle.status).toBe('idle')
})

test('done sets result and status done', () => {
  const s = run(sequence)
  expect(s.status).toBe('done')
  expect(s.result).toBe(done.result)
  expect(s.totals).toEqual(totals)
  expect(isTerminal(s)).toBe(true)
  expect(isTerminal(run([runStarted]))).toBe(false)
})

test('failed sets failure with limit', () => {
  const s = run([runStarted, step, called, returned, usage, failed])
  expect(s.status).toBe('failed')
  expect(s.failure).toEqual(failed)
  expect(s.failure?.limit).toBe('tokens')
  expect(isTerminal(s)).toBe(true)
})

test('a full replay from 0 equals the live sequence', () => {
  const live = run(sequence)
  let replay = reduce(initialLiveState, { kind: 'start', runId: 'r1' })
  for (const event of [...sequence.slice(0, 3), ...sequence]) {
    replay = reduce(replay, { kind: 'event', event })
  }
  expect(replay).toEqual(live)
})

test('start resets everything', () => {
  const s = reduce(run(sequence), { kind: 'start', runId: 'r2' })
  expect(s).toEqual({ ...initialLiveState, runId: 'r2', status: 'running' })
})

test('reduce does not mutate the previous state or the event', () => {
  const prev = run([runStarted, called])
  const snapshot = structuredClone(prev)
  const evSnap = structuredClone(step)
  reduce(prev, { kind: 'event', event: step })
  reduce(prev, { kind: 'event', event: returned })
  reduce(prev, { kind: 'connection_lost' })
  expect(prev).toEqual(snapshot)
  expect(step).toEqual(evSnap)
})

test('connection_gone moves running and reconnecting to lost', () => {
  const running = run([runStarted])
  const lost = reduce(running, { kind: 'connection_gone' })
  expect(lost.status).toBe('lost')
  expect(isTerminal(lost)).toBe(true)
  const reconnecting = reduce(running, { kind: 'connection_lost' })
  expect(reduce(reconnecting, { kind: 'connection_gone' }).status).toBe('lost')
})

test('connection_gone never touches done or failed', () => {
  const d = run(sequence)
  expect(reduce(d, { kind: 'connection_gone' })).toBe(d)
  const f = run([...sequence.slice(0, 5), failed])
  expect(reduce(f, { kind: 'connection_gone' })).toBe(f)
})
