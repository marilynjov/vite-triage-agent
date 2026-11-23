/**
 * The eval dashboard at #/eval: one GET of /eval/summary, five plain tables, no chart
 * library. Every number comes from files the evals already wrote; nothing here spends.
 */
import { useEffect, useState } from 'react'
import { API } from './api'
import type { EvalSummary } from './types.generated'

const pct = (x: number | null) => (x === null ? '—' : `${(x * 100).toFixed(1)}%`)
const usd = (x: number | null) => (x === null ? '—' : `$${x.toFixed(4)}`)

type Loaded = { kind: 'ok'; summary: EvalSummary } | { kind: 'error'; message: string }

function Section({ title, empty, children }: { title: string; empty: boolean; children: React.ReactNode }) {
  return (
    <section className="eval-section">
      <h2>{title}</h2>
      {empty ? <p className="hint">No data yet</p> : children}
    </section>
  )
}

function Nightly({ s }: { s: EvalSummary }) {
  return (
    <Section title="Nightly retrieval (val, clean)" empty={s.nightly.length === 0}>
      <table className="trace">
        <thead>
          <tr><th>at</th><th>strategy</th><th>n</th><th>recall@10</th><th>MRR</th><th>status</th></tr>
        </thead>
        <tbody>
          {[...s.nightly].reverse().map((p) => (
            <tr key={p.at} className={!p.ok ? 'trace-error' : p.changed ? 'eval-changed' : undefined}>
              <td>{p.at.replace('T', ' ').slice(0, 16)}</td>
              <td>{p.strategy}</td>
              <td>{p.n}</td>
              <td>{pct(p.recall_at_10_clean)}</td>
              <td>{pct(p.mrr_clean)}</td>
              <td>{!p.ok ? `failed: ${p.error}` : p.changed ? 'changed' : 'same'}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </Section>
  )
}

function Arms({ s }: { s: EvalSummary }) {
  return (
    <Section title={`Arms on val, clean (${s.arms_source})`} empty={s.arms.length === 0}>
      <table className="trace">
        <thead>
          <tr><th>arm</th><th>runs</th><th>recall@1</th><th>recall@5</th><th>MRR</th><th>$/triage</th></tr>
        </thead>
        <tbody>
          {s.arms.map((a) => (
            <tr key={a.arm}>
              <td>{a.arm}</td>
              <td>{a.runs}</td>
              <td>{pct(a.recall_at_1)}</td>
              <td>{pct(a.recall_at_5)}</td>
              <td>{pct(a.mrr)}</td>
              <td>{usd(a.per_triage)}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </Section>
  )
}

function Shares({ title, rows, note }: { title: string; rows: EvalSummary['staged_outcomes']; note?: string }) {
  const withN = rows.some((r) => r.n !== null)
  return (
    <Section title={title} empty={rows.length === 0}>
      <table className="trace">
        <thead>
          <tr><th>outcome</th>{withN && <th>runs</th>}<th>share</th></tr>
        </thead>
        <tbody>
          {rows.map((r) => (
            <tr key={r.name}>
              <td>{r.name}</td>
              {withN && <td>{r.n}</td>}
              <td>{pct(r.share)}</td>
            </tr>
          ))}
        </tbody>
      </table>
      {note && <p className="hint">{note}</p>}
    </Section>
  )
}

function Taxonomy({ s }: { s: EvalSummary }) {
  const total = s.taxonomy.reduce((sum, t) => sum + t.n, 0)
  return (
    <Section title={`Failure taxonomy (${total} labelled agent misses)`} empty={s.taxonomy.length === 0}>
      <table className="trace">
        <thead>
          <tr><th>category</th><th>failures</th></tr>
        </thead>
        <tbody>
          {s.taxonomy.map((t) => (
            <tr key={t.category}>
              <td>{t.category}</td>
              <td>{t.n}</td>
            </tr>
          ))}
        </tbody>
      </table>
      <p className="hint">Definitions and evidence: docs/failure-taxonomy.md</p>
    </Section>
  )
}

export function EvalView() {
  const [loaded, setLoaded] = useState<Loaded | null>(null)

  useEffect(() => {
    let cancelled = false
    fetch(`${API}/eval/summary`)
      .then(async (r) => {
        if (!r.ok) throw new Error(r.statusText)
        return (await r.json()) as EvalSummary
      })
      .then((summary) => {
        if (!cancelled) setLoaded({ kind: 'ok', summary })
      })
      .catch((caught) => {
        if (!cancelled) setLoaded({ kind: 'error', message: caught instanceof Error ? caught.message : String(caught) })
      })
    return () => {
      cancelled = true
    }
  }, [])

  return (
    <main className="runs-page eval-page">
      <h1>Eval</h1>
      {loaded === null && <p className="hint">Loading…</p>}
      {loaded?.kind === 'error' && <p className="error">Failed to load: {loaded.message}</p>}
      {loaded?.kind === 'ok' && (
        <>
          <Nightly s={loaded.summary} />
          <Arms s={loaded.summary} />
          <Shares title="Staged outcomes (val-n40-r3, clean)" rows={loaded.summary.staged_outcomes} />
          <Shares
            title="Patch outcomes (fail-to-pass)"
            rows={loaded.summary.patch_outcomes}
            note={`Pass rate over patchable runs (stale paths left out): ${pct(loaded.summary.patch_pass_rate)}`}
          />
          <Taxonomy s={loaded.summary} />
        </>
      )}
    </main>
  )
}
