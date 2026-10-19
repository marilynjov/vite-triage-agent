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
import { useState } from 'react'
import type { TriageResult } from './types.generated'
import './App.css'

const API = 'http://localhost:8000'

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

export default function App() {
  const [title, setTitle] = useState('')
  const [body, setBody] = useState('')
  const [naive, setNaive] = useState<NaiveResponse | null>(null)
  const [structured, setStructured] = useState<StructuredResponse | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [pending, setPending] = useState<Mode | null>(null)

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
