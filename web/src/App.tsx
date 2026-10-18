/**
 * Phase 01's whole UI: paste an issue, get prose back, see what it cost.
 *
 * useState only. No component library, no state library, no data-fetching
 * library — the point is that every behaviour on this screen is accountable to
 * a line in this file. TanStack Query arrives in Phase 03, the week its problem
 * can be named in this codebase.
 *
 * These two types are hand-written today and WILL be generated from the Python
 * schema in Phase 02, so that a backend change breaks the build instead of
 * production. Until then they are a promise, not a contract.
 */
import { useState } from 'react'
import './App.css'

const API = 'http://localhost:8000'

type TriageResponse = {
  answer: string
  call_id: number
  model: string
  provider: string
  input_tokens: number
  output_tokens: number
  latency_ms: number
  cost_usd: number
}

function money(usd: number): string {
  if (usd === 0) return 'free'
  // Four decimals hides a Haiku call entirely, so go to six.
  return '$' + usd.toFixed(6)
}

export default function App() {
  const [title, setTitle] = useState('')
  const [body, setBody] = useState('')
  const [result, setResult] = useState<TriageResponse | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [pending, setPending] = useState(false)

  async function triage(event: React.FormEvent) {
    event.preventDefault()
    setPending(true)
    setError(null)
    setResult(null)

    try {
      const response = await fetch(API + '/triage/naive', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ title, body }),
      })
      const payload = await response.json()
      if (!response.ok) {
        // The API puts the real reason in `detail` — Ollama being down, a
        // placeholder key, a refusal. Show it rather than "something broke".
        throw new Error(payload.detail ?? response.statusText)
      }
      setResult(payload as TriageResponse)
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : String(caught))
    } finally {
      setPending(false)
    }
  }

  return (
    <main>
      <h1>Triage</h1>
      <p className="lede">
        Naive baseline — the issue text goes in, prose comes out. No retrieval,
        no tools. Every later phase is measured against this.
      </p>

      <form onSubmit={triage}>
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
            rows={12}
            placeholder="Paste the bug report as it was filed…"
          />
        </label>

        <button type="submit" disabled={pending || !title.trim() || !body.trim()}>
          {pending ? 'Triaging…' : 'Triage'}
        </button>
        {pending && (
          <span className="hint">
            a local model takes ~15s; the API takes ~2s
          </span>
        )}
      </form>

      {error && (
        <section className="error">
          <h2>Failed</h2>
          <p>{error}</p>
        </section>
      )}

      {result && (
        <section className="result">
          <dl className="meters">
            <div>
              <dt>provider</dt>
              <dd>{result.provider}</dd>
            </div>
            <div>
              <dt>model</dt>
              <dd>{result.model}</dd>
            </div>
            <div>
              <dt>tokens in / out</dt>
              <dd>
                {result.input_tokens} / {result.output_tokens}
              </dd>
            </div>
            <div>
              <dt>latency</dt>
              <dd>{(result.latency_ms / 1000).toFixed(1)}s</dd>
            </div>
            <div>
              <dt>cost</dt>
              <dd>{money(result.cost_usd)}</dd>
            </div>
            <div>
              <dt>call</dt>
              <dd>#{result.call_id}</dd>
            </div>
          </dl>

          {/* Deliberately <pre>, not rendered markdown. The model invents its
              own headings because prose has no contract — seeing that raw is
              the argument for Phase 02. */}
          <pre className="answer">{result.answer}</pre>
        </section>
      )}
    </main>
  )
}
