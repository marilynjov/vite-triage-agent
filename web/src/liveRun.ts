/**
 * The live trace as a pure reducer over the SSE event stream.
 *
 * Events carry a per-run `seq` numbered from 1. An event is applied only when
 * seq === lastSeq + 1; later ones wait in `pending`; seq <= lastSeq are
 * duplicates (a reconnect replays) and are dropped. Nothing here mutates its
 * inputs. Generated types are imported, never redeclared.
 */
import type {
  Failed,
  GroundedTriageResult,
  LiveCandidate,
  LiveEvent,
  Totals,
} from './types.generated'

export type LiveStatus = 'idle' | 'running' | 'reconnecting' | 'done' | 'failed'

export interface LiveCall {
  toolUseId: string
  turn: number
  name: string
  input: Record<string, unknown>
  output: string | null
  isError: boolean | null
  durationMs: number | null
}

export interface LiveState {
  runId: string | null
  status: LiveStatus
  lastSeq: number
  pending: Record<number, LiveEvent>
  candidates: LiveCandidate[]
  calls: LiveCall[]
  totals: Totals | null
  result: GroundedTriageResult | null
  failure: Failed | null
}

export type LiveAction =
  | { kind: 'start'; runId: string }
  | { kind: 'event'; event: LiveEvent }
  | { kind: 'connection_lost' }
  | { kind: 'connection_open' }

export const initialLiveState: LiveState = {
  runId: null,
  status: 'idle',
  lastSeq: 0,
  pending: {},
  candidates: [],
  calls: [],
  totals: null,
  result: null,
  failure: null,
}

export function isTerminal(s: LiveState): boolean {
  return s.status === 'done' || s.status === 'failed'
}

/** Apply one in-order event; returns a new state. */
function apply(state: LiveState, event: LiveEvent): LiveState {
  const next: LiveState = { ...state, lastSeq: event.seq }
  switch (event.type) {
    case 'run_started':
      return { ...next, candidates: event.candidates }
    case 'step_started':
      return next
    case 'tool_called':
      return {
        ...next,
        calls: [
          ...state.calls,
          {
            toolUseId: event.tool_use_id,
            turn: event.turn,
            name: event.name,
            input: event.input,
            output: null,
            isError: null,
            durationMs: null,
          },
        ],
      }
    case 'tool_returned':
      return {
        ...next,
        calls: state.calls.map((c) =>
          c.toolUseId === event.tool_use_id
            ? {
                ...c,
                output: event.output_preview,
                isError: event.is_error,
                durationMs: event.duration_ms,
              }
            : c,
        ),
      }
    case 'usage':
      return { ...next, totals: event.totals }
    case 'done':
      return { ...next, status: 'done', result: event.result, totals: event.totals }
    case 'failed':
      return { ...next, status: 'failed', failure: event, totals: event.totals }
  }
}

function receive(state: LiveState, event: LiveEvent): LiveState {
  if (event.seq <= state.lastSeq) return state
  if (event.seq > state.lastSeq + 1) {
    return { ...state, pending: { ...state.pending, [event.seq]: event } }
  }
  let s = apply(state, event)
  let buffered = s.pending[s.lastSeq + 1]
  while (buffered !== undefined) {
    const { [buffered.seq]: _taken, ...rest } = s.pending
    s = apply({ ...s, pending: rest }, buffered)
    buffered = s.pending[s.lastSeq + 1]
  }
  return s
}

export function reduce(state: LiveState, action: LiveAction): LiveState {
  switch (action.kind) {
    case 'start':
      return { ...initialLiveState, runId: action.runId, status: 'running' }
    case 'event':
      return receive(state, action.event)
    case 'connection_lost':
      return state.status === 'running' ? { ...state, status: 'reconnecting' } : state
    case 'connection_open':
      return state.status === 'reconnecting' ? { ...state, status: 'running' } : state
  }
}
