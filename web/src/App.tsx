/**
 * Phase 01 and 02 side by side: the same issue, prose against a typed object.
 *
 * useState only. No component library, no state library, no data-fetching
 * library — every behaviour on this screen is accountable to a line in this
 * file. TanStack Query arrives in Phase 03, the week its problem can be named
 * in this codebase.
 *
 * TriageResult is imported, never redeclared. It is generated from
 * api/schema.py by scripts/gen_types.py, so a backend field change breaks this
 * build instead of production. That is Phase 02's whole claim, and the proof is
 * below: not one `?.` or `??` guards a field of it, because the schema makes
 * all six required, Pydantic validates before the response leaves FastAPI, and
 * TypeScript can therefore prove they are there.
 */
import { useEffect, useReducer, useRef, useState } from 'react'
import type { LiveEvent, TriageResult } from './types.generated'
import {
  initialLiveState,
  isTerminal,
  reduce,
  type LiveAction,
  type LiveCall,
  type LiveState,
} from './liveRun'
import './App.css'

const API = 'http://localhost:8000'
const RUN_KEY = 'live-run-id'
const EVENT_TYPES: LiveEvent['type'][] = [
  'run_started',
  'step_started',
  'usage',
  'tool_called',
  'tool_returned',
  'done',
  'failed',
]

/** Shared by both endpoints: what the call cost, regardless of what it returned. */
type Envelope = {
  call_id: number
  model: string
  provider: string
  input_tokens: number
  output_tokens: number
  latency_ms: number
  cost_usd: number
}

type NaiveResponse = Envelope & { answer: string }
type StructuredResponse = Envelope & { result: TriageResult }


type Limits = { max_turns: number; max_tokens: number; max_seconds: number; max_dollars: number }

type Mode = 'naive' | 'structured'

function money(usd: number): string {
  return usd === 0 ? 'free' : '$' + usd.toFixed(6)
}

function Meters({ envelope }: { envelope: Envelope }) {
  const cells: [string, string][] = [
    ['provider', envelope.provider],
    ['model', envelope.model],
    ['tokens in / out', `${envelope.input_tokens} / ${envelope.output_tokens}`],
    ['latency', `${(envelope.latency_ms / 1000).toFixed(1)}s`],
    ['cost', money(envelope.cost_usd)],
    ['call', `#${envelope.call_id}`],
  ]
  return (
    <dl className="meters">
      {cells.map(([label, value]) => (
        <div key={label}>
          <dt>{label}</dt>
          <dd>{value}</dd>
        </div>
      ))}
    </dl>
  )
}

/**
 * The gate. Six fields, read directly, no guards.
 *
 * If you ever need `r.severity ?? 'unknown'` here, the contract is wrong — fix
 * api/schema.py and regenerate. Do not patch it in the UI.
 */
function Triage({ r }: { r: TriageResult }) {
  return (
    <div className="triage">
      <div className="verdict">
        <span className={`chip type-${r.issue_type}`}>{r.issue_type}</span>
        <span className={`chip sev-${r.severity}`}>severity: {r.severity}</span>
        <label className="conf">
          confidence
          <meter value={r.confidence} min={0} max={1} />
          <span className="mono">{(r.confidence * 100).toFixed(0)}%</span>
        </label>
      </div>

      <h3>Suspected components</h3>
      {r.suspected_components.length > 0 ? (
        <ul className="components">
          {r.suspected_components.map((c) => (
            <li key={c} className="mono">{c}</li>
          ))}
        </ul>
      ) : (
        <p className="empty">none identified</p>
      )}

      <h3>Reproduction steps</h3>
      {r.reproduction_steps.length > 0 ? (
        <ol className="steps">
          {r.reproduction_steps.map((s, i) => (
            <li key={i}>{s}</li>
          ))}
        </ol>
      ) : (
        <p className="empty">none given in the report</p>
      )}

      <h3>Reasoning</h3>
      <p className="reasoning">{r.reasoning}</p>
    </div>
  )
}


function storeRunId(id: string | null) {
  try {
    if (id === null) localStorage.removeItem(RUN_KEY)
    else localStorage.setItem(RUN_KEY, id)
  } catch {
    // storage blocked: the run still works, it just won't reopen after a reload
  }
}

function loadRunId(): string | null {
  try {
    return localStorage.getItem(RUN_KEY)
  } catch {
    return null
  }
}

/**
 * Open the run's event stream. Events carry no run id, so a stale source's
 * callbacks must be ignored here: every handler first checks that its source
 * is still the current one in the ref.
 */
function openStream(
  runId: string,
  after: number,
  dispatch: (a: LiveAction) => void,
  sourceRef: { current: EventSource | null },
) {
  const source = new EventSource(`${API}/runs/${runId}/events?after=${after}`)
  sourceRef.current = source
  const current = () => sourceRef.current === source
  source.onopen = () => {
    if (current()) dispatch({ kind: 'connection_open' })
  }
  source.onerror = () => {
    if (!current()) return
    // CLOSED means the server refused the reconnect (404: expired or restarted);
    // the browser will not retry.
    if (source.readyState === EventSource.CLOSED) {
      source.close()
      sourceRef.current = null
      storeRunId(null)
      dispatch({ kind: 'connection_gone' })
      return
    }
    dispatch({ kind: 'connection_lost' })
  }
  // The server sends named events, which onmessage never sees.
  for (const type of EVENT_TYPES) {
    source.addEventListener(type, (m) => {
      if (!current()) return
      const event = JSON.parse((m as MessageEvent<string>).data) as LiveEvent
      dispatch({ kind: 'event', event })
    })
  }
}

function LiveMeters({ live, limits }: { live: LiveState; limits: Limits | null }) {
  const t = live.totals
  const turns = t === null ? 0 : t.turns
  const tokens = t === null ? 0 : t.tokens
  const dollars = t === null ? 0 : t.cost_usd
  const seconds = t === null ? 0 : t.seconds
  const cells: [string, string][] = [
    ['status', live.status],
    ['turns', limits === null ? String(turns) : `${turns} / ${limits.max_turns}`],
    ['tokens', limits === null ? String(tokens) : `${tokens} / ${limits.max_tokens}`],
    ['dollars', limits === null ? money(dollars) : `${money(dollars)} / $${limits.max_dollars}`],
    ['seconds', limits === null ? seconds.toFixed(1) : `${seconds.toFixed(1)} / ${limits.max_seconds}`],
    ['run', live.runId === null ? '' : live.runId],
  ]
  return (
    <dl className="meters">
      {cells.map(([label, value]) => (
        <div key={label}>
          <dt>{label}</dt>
          <dd>{value}</dd>
        </div>
      ))}
    </dl>
  )
}

function LivePanel({ live, limits }: { live: LiveState; limits: Limits | null }) {
  const retrieved = new Set(live.candidates.map((c) => c.path))
  const looked = (f: string) =>
    live.calls.some(
      (c: LiveCall) =>
        JSON.stringify(c.input).includes(f) || (c.output !== null && c.output.includes(f)),
    )
  const failure = live.failure
  return (
    <>
      <LiveMeters live={live} limits={limits} />

      {live.status === 'reconnecting' && <p className="hint">connection lost, reconnecting…</p>}
      {live.status === 'lost' && (
        <p className="hint">
          This run is no longer available on the server (it expired or the API
          restarted). Start a new run.
        </p>
      )}

      {failure !== null && (
        <section className="error">
          <h2>Run failed: {failure.reason}</h2>
          <p className="mono">{failure.message}</p>
          {failure.reason === 'budget' && failure.limit !== null && failure.value !== null && (
            <p className="mono">
              limit: {failure.limit} = {failure.value}
            </p>
          )}
        </section>
      )}

      {live.result !== null && (
        <>
          <Triage r={live.result} />
          <h3>Suspected files</h3>
          {live.result.suspected_files.length > 0 ? (
            <ul className="components">
              {live.result.suspected_files.map((f) => (
                <li key={f} className="mono">
                  {f}{' '}
                  <span className={retrieved.has(f) ? 'tag' : looked(f) ? 'tag found' : 'tag'}>
                    {retrieved.has(f)
                      ? 'in candidates'
                      : looked(f)
                        ? 'found by looking'
                        : 'outside candidates'}
                  </span>
                </li>
              ))}
            </ul>
          ) : (
            <p className="empty">none identified</p>
          )}
        </>
      )}

      <h3>Trace</h3>
      {live.calls.length > 0 ? (
        <table className="trace">
          <thead>
            <tr>
              <th>turn</th>
              <th>tool</th>
              <th>arguments</th>
              <th>result</th>
              <th>ms</th>
            </tr>
          </thead>
          <tbody>
            {live.calls.map((c) => (
              <tr key={c.toolUseId} className={c.isError === true ? 'trace-error' : undefined}>
                <td className="mono">{c.turn}</td>
                <td className="mono">{c.name}</td>
                <td className="mono">{JSON.stringify(c.input)}</td>
                <td>
                  {c.output === null ? (
                    <span className="hint">running…</span>
                  ) : (
                    <details>
                      <summary>{c.isError === true ? 'error' : `${c.output.length} chars`}</summary>
                      <pre className="answer">{c.output}</pre>
                    </details>
                  )}
                </td>
                <td className="mono">{c.durationMs === null ? '' : c.durationMs}</td>
              </tr>
            ))}
          </tbody>
        </table>
      ) : (
        <p className="empty">
          {live.status === 'done'
            ? 'the model answered without calling a tool'
            : isTerminal(live)
              ? 'no tool calls'
              : 'waiting for the first tool call'}
        </p>
      )}
    </>
  )
}

export default function App() {
  const [title, setTitle] = useState('')
  const [body, setBody] = useState('')
  const [naive, setNaive] = useState<NaiveResponse | null>(null)
  const [structured, setStructured] = useState<StructuredResponse | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [pending, setPending] = useState<Mode | null>(null)
  const [live, dispatch] = useReducer(reduce, initialLiveState)
  const [limits, setLimits] = useState<Limits | null>(null)
  const sourceRef = useRef<EventSource | null>(null)
  const startingRef = useRef(false)
  const [starting, setStarting] = useState(false)

  function closeSource() {
    if (sourceRef.current !== null) sourceRef.current.close()
    sourceRef.current = null
  }

  // Reopen on load: limits, then the remembered run if the server still has it.
  useEffect(() => {
    let cancelled = false
    fetch(`${API}/runs/limits`)
      .then((r) => (r.ok ? r.json() : null))
      .then((l) => {
        if (!cancelled && l !== null) setLimits(l as Limits)
      })
      .catch(() => {})
    const id = loadRunId()
    if (id !== null) {
      fetch(`${API}/runs/${id}`)
        .then((r) => {
          if (cancelled) return
          if (r.status === 404) {
            storeRunId(null)
            return
          }
          if (!r.ok) return
          closeSource()
          dispatch({ kind: 'start', runId: id })
          openStream(id, 0, dispatch, sourceRef)
        })
        .catch(() => {})
    }
    return () => {
      cancelled = true
      closeSource()
    }
  }, [])

  // The server ends the stream after done/failed; without this the browser
  // would reconnect forever.
  useEffect(() => {
    if (isTerminal(live)) closeSource()
  }, [live])

  async function startAgent() {
    if (startingRef.current) return // a double-click must not POST twice
    startingRef.current = true
    setStarting(true)
    setError(null)
    try {
      const response = await fetch(`${API}/runs`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ title, body }),
      })
      const payload = await response.json()
      if (!response.ok) {
        throw new Error(typeof payload.detail === 'string' ? payload.detail : response.statusText)
      }
      const runId = (payload as { run_id: string }).run_id
      storeRunId(runId)
      closeSource() // before start: a stale source must not feed the new run
      dispatch({ kind: 'start', runId })
      openStream(runId, 0, dispatch, sourceRef)
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : String(caught))
    } finally {
      startingRef.current = false
      setStarting(false)
    }
  }

  async function triage(mode: Mode) {
    setPending(mode)
    setError(null)
    if (mode === 'naive') setNaive(null)
    else setStructured(null)

    try {
      const response = await fetch(`${API}/triage/${mode}`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ title, body }),
      })
      const payload = await response.json()
      if (!response.ok) {
        // An error body is the one thing with no contract — a 500 from a
        // contract violation carries `detail`, a proxy failure might not.
        throw new Error(payload.detail ?? response.statusText)
      }
      if (mode === 'naive') setNaive(payload as NaiveResponse)
      else setStructured(payload as StructuredResponse)
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : String(caught))
    } finally {
      setPending(null)
    }
  }

  const ready = title.trim().length > 0 && body.trim().length > 0
  const liveBusy = live.status === 'running' || live.status === 'reconnecting'

  return (
    <main>
      <h1>Triage</h1>
      <p className="lede">
        The same issue, twice. Prose has no contract and the model invents a new
        shape every run; the typed result is guaranteed by a schema shared with
        the backend.
      </p>

      <form onSubmit={(e) => e.preventDefault()}>
        <label>
          Issue title
          <input
            value={title}
            onChange={(e) => setTitle(e.target.value)}
            placeholder="CSS @import url() fails to resolve"
          />
        </label>

        <label>
          Issue body
          <textarea
            value={body}
            onChange={(e) => setBody(e.target.value)}
            rows={10}
            placeholder="Paste the bug report as it was filed…"
          />
        </label>

        <div className="actions">
          <button type="button" onClick={() => triage('structured')} disabled={!ready || pending !== null}>
            {pending === 'structured' ? 'Triaging…' : 'Typed triage'}
          </button>
          <button type="button" className="secondary" onClick={() => triage('naive')} disabled={!ready || pending !== null}>
            {pending === 'naive' ? 'Triaging…' : 'Prose (Phase 01)'}
          </button>
          <button type="button" className="secondary" onClick={startAgent} disabled={!ready || liveBusy || starting}>
            {liveBusy || starting ? 'Investigating…' : 'Agent (live)'}
          </button>
          {pending && <span className="hint">a local model takes ~15s; the API ~3s</span>}
        </div>
      </form>

      {error && (
        <section className="error">
          <h2>Failed</h2>
          <p className="mono">{error}</p>
        </section>
      )}

      {structured && (
        <section className="result">
          <h2>Typed</h2>
          <Meters envelope={structured} />
          <Triage r={structured.result} />
        </section>
      )}

      {live.status !== 'idle' && (
        <section className="result">
          <h2>Agent</h2>
          <LivePanel live={live} limits={limits} />
        </section>
      )}

      {naive && (
        <section className="result">
          <h2>Prose</h2>
          <Meters envelope={naive} />
          {/* Deliberately <pre>, not rendered markdown: the model picks its own
              headings every run, and seeing that raw is the argument above. */}
          <pre className="answer">{naive.answer}</pre>
        </section>
      )}
    </main>
  )
}
