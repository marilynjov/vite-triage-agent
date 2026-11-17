/**
 * Phase 06c: the run browser. One stored staged run, stage by stage, ending
 * with the agent's diff next to the real fix's.
 *
 * Everything here reads records the 06a/06b evals already wrote: no model
 * call, $0. Selection lives in the hash (#/runs/<issue>/<repeat>), so a run is
 * linkable and the back button works without a router library.
 */
import { useEffect, useState } from 'react'
import { API } from './api'
import { parseDiff } from './diff'
import { filterRuns, type RunFilter } from './runFilter'
import { runHash } from './router'
import type { StagedRunDetail, StagedRunSummary } from './types.generated'

function usd(n: number): string {
  return '$' + n.toFixed(4)
}

function outcomeClass(outcome: string): string {
  if (outcome === 'hit') return 'out-hit'
  if (outcome.startsWith('error')) return 'out-error'
  return 'out-miss'
}

const PATCH_CHIP: Record<string, string> = {
  pass: '✓ pass',
  test_fail: '✗ test_fail',
  stale_path: 'stale',
  patch_error: 'error',
}

function PatchChip({ outcome }: { outcome: string | null }) {
  const label = outcome === null ? 'none' : (PATCH_CHIP[outcome] ?? outcome)
  return <span className={`chip patch-${outcome ?? 'none'}`}>{label}</span>
}

function RunList({
  runs,
  selected,
}: {
  runs: StagedRunSummary[]
  selected: string | null
}) {
  const [filter, setFilter] = useState<RunFilter>({ outcome: '', patchedOnly: false })
  const outcomes = [...new Set(runs.map((r) => r.outcome))].sort()
  const shown = filterRuns(runs, filter)
  return (
    <nav className="run-list" aria-label="Stored staged runs">
      <div className="run-filters">
        <label>
          outcome
          <select value={filter.outcome} onChange={(e) => setFilter({ ...filter, outcome: e.target.value })}>
            <option value="">any</option>
            {outcomes.map((o) => (
              <option key={o} value={o}>{o}</option>
            ))}
          </select>
        </label>
        <label>
          <input
            type="checkbox"
            checked={filter.patchedOnly}
            onChange={(e) => setFilter({ ...filter, patchedOnly: e.target.checked })}
          />
          patched only
        </label>
        <span className="hint">{shown.length} of {runs.length}</span>
      </div>
      <ul>
        {shown.map((r) => {
          const hash = runHash(r.issue, r.repeat)
          return (
            <li key={hash}>
              <a href={hash} aria-current={hash === selected ? 'true' : undefined}>
                <span className="mono">#{r.issue} r{r.repeat}</span>
                <span className="run-title">{r.title}</span>
                <span className="run-chips">
                  <span className={`chip ${outcomeClass(r.outcome)}`}>{r.outcome}</span>
                  <PatchChip outcome={r.patch_outcome} />
                </span>
              </a>
            </li>
          )
        })}
      </ul>
    </nav>
  )
}

const ANALYSIS = ['locate', 'understand', 'hypothesize'] as const
type CardState = 'ok' | 'failed' | 'unreached'

function StageCard({
  name,
  cost,
  state,
  error,
  children,
}: {
  name: string
  cost: number | null
  state: CardState
  error?: string | null
  children?: React.ReactNode
}) {
  return (
    <section className={`stage stage-${state}`}>
      <h3>
        {name}
        {cost !== null && <span className="mono stage-cost">{usd(cost)}</span>}
      </h3>
      {state === 'unreached' ? (
        <p className="empty">not reached</p>
      ) : (
        <>
          {state === 'failed' && error && <p className="mono stage-error">{error}</p>}
          {children}
        </>
      )}
    </section>
  )
}

function span(path: string, start: number, end: number): string {
  return `${path}:${start}–${end}`
}

function verifyLabel(v: NonNullable<NonNullable<StagedRunDetail['patch']>['verify']>): string {
  if (v.timed_out) return 'TIMEOUT'
  if (v.status === 'pass') return 'PASS'
  if (v.status === 'test_fail') return 'FAIL'
  return v.status
}

function Stages({ run }: { run: StagedRunDetail }) {
  const failedAt = run.failed_stage === null ? -1 : ANALYSIS.indexOf(run.failed_stage as (typeof ANALYSIS)[number])
  const state = (i: number): CardState =>
    failedAt === -1 || i < failedAt ? 'ok' : i === failedAt ? 'failed' : 'unreached'
  const gold = new Set(run.gold)
  const { locate, understand, hypothesis } = run.stages
  const patch = run.patch
  const cost = (k: string) => run.stage_costs[k] ?? null
  return (
    <>
      <StageCard name="locate" cost={cost('locate')} state={state(0)} error={run.error}>
        {locate && (
          <ul className="stage-list mono">
            {locate.files.map((f) => (
              <li key={f.path}>
                {f.path}
                {gold.has(f.path) && <span className="tag found">✓ gold</span>}
              </li>
            ))}
          </ul>
        )}
      </StageCard>

      <StageCard name="understand" cost={cost('understand')} state={state(1)} error={run.error}>
        {understand && (
          <ul className="stage-list">
            {understand.regions.map((r, i) => (
              <li key={i}>
                <span className="mono">{span(r.path, r.start_line, r.end_line)}</span>
                <p>{r.relevance}</p>
              </li>
            ))}
          </ul>
        )}
      </StageCard>

      <StageCard name="hypothesize" cost={cost('hypothesize')} state={state(2)} error={run.error}>
        {hypothesis && (
          <>
            <p className="mono">{span(hypothesis.fault.path, hypothesis.fault.start_line, hypothesis.fault.end_line)}</p>
            <p>{hypothesis.fault.mechanism}</p>
            <p className="hint">
              fault hit: {run.fault_hit === null ? 'n.a.' : run.fault_hit ? 'yes' : 'no'} · confidence{' '}
              {(hypothesis.confidence * 100).toFixed(0)}%
            </p>
          </>
        )}
      </StageCard>

      {patch !== null && (
        <>
          <StageCard name="patch" cost={patch.cost_usd} state={patch.outcome === 'patch_error' ? 'failed' : 'ok'} error={patch.error}>
            {patch.reasoning !== null && <p className="reasoning">{patch.reasoning}</p>}
            <p className="hint">
              {patch.edits.length} edit{patch.edits.length === 1 ? '' : 's'} · outcome {patch.outcome ?? 'none'}
            </p>
          </StageCard>

          <StageCard name="verify" cost={null} state={patch.verify === null ? 'unreached' : patch.verify.passed ? 'ok' : 'failed'}>
            {patch.verify !== null && (
              <>
                <p>
                  <strong>{verifyLabel(patch.verify)}</strong>{' '}
                  <span className="hint">{patch.verify.seconds.toFixed(1)}s</span>
                </p>
                <details>
                  <summary>test output</summary>
                  <pre className="answer">{patch.verify.tail}</pre>
                </details>
              </>
            )}
          </StageCard>
        </>
      )}
    </>
  )
}

function DiffView({ text }: { text: string }) {
  return (
    <>
      {parseDiff(text).map((f) => (
        <div key={f.path} className="diff-file">
          <div className="diff-path mono">{f.path}</div>
          <pre className="diff-lines">
            {f.lines.map((l, i) => (
              <div key={i} className={`dl ${l.kind}`}>
                {l.kind === 'add' ? '+' : l.kind === 'del' ? '-' : l.kind === 'ctx' ? ' ' : ''}
                {l.text}
              </div>
            ))}
          </pre>
        </div>
      ))}
    </>
  )
}

function agentMissing(run: StagedRunDetail): string | null {
  const p = run.patch
  if (p === null) return 'no patch'
  if (p.outcome === 'stale_path') return 'no patch (stale path)'
  if (p.outcome === 'patch_error') return 'patch error'
  if (!p.diff) return 'no patch'
  return null
}

function realMissing(run: StagedRunDetail): string | null {
  if (run.real_fix === null) return 'fix commit unknown'
  if (run.real_fix.diff === '') return 'no source changes'
  return null
}

function DiffPane({ run }: { run: StagedRunDetail }) {
  const agent = agentMissing(run)
  const real = realMissing(run)
  return (
    <div className="diffs">
      <section>
        <h3>Agent</h3>
        {agent !== null ? <p className="empty">{agent}</p> : <DiffView text={run.patch?.diff ?? ''} />}
      </section>
      <section>
        <h3>
          Real fix
          {run.real_fix !== null && <span className="mono stage-cost">{run.real_fix.sha.slice(0, 7)}</span>}
        </h3>
        {real !== null ? <p className="empty">{real}</p> : <DiffView text={run.real_fix?.diff ?? ''} />}
      </section>
    </div>
  )
}

function RunDetail({ run }: { run: StagedRunDetail }) {
  return (
    <article className="run-detail">
      <header>
        <h2>
          <a href={`https://github.com/vitejs/vite/issues/${run.issue}`} target="_blank" rel="noreferrer">
            #{run.issue}
          </a>{' '}
          {run.title}
        </h2>
        <p className="hint">
          repeat {run.repeat} · <span className={`chip ${outcomeClass(run.outcome)}`}>{run.outcome}</span> · total{' '}
          <span className="mono">{usd(run.cost_usd)}</span>
        </p>
        <details>
          <summary>issue body</summary>
          <pre className="answer">{run.body}</pre>
        </details>
      </header>

      <Stages run={run} />
      <DiffPane run={run} />

      <details className="run-tools">
        <summary>Tool calls ({run.tool_calls.length}) — stored as one list, not grouped by stage</summary>
        <table className="trace">
          <tbody>
            {run.tool_calls.map((c, i) => (
              <tr key={i} className={c.is_error ? 'trace-error' : undefined}>
                <td className="mono">{c.name}</td>
                <td className="mono">{JSON.stringify(c.input)}</td>
                <td>
                  <details>
                    <summary>output</summary>
                    <pre>{c.output_preview}</pre>
                  </details>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </details>
    </article>
  )
}

/** What the last detail fetch returned, tagged with the run it was for. */
type Fetched = { key: string } & (
  | { kind: 'missing' }
  | { kind: 'error'; message: string }
  | { kind: 'ok'; run: StagedRunDetail }
)

function message(caught: unknown): string {
  return caught instanceof Error ? caught.message : String(caught)
}

export function RunsView({ issue, repeat }: { issue?: number; repeat?: number }) {
  const [runs, setRuns] = useState<StagedRunSummary[] | null>(null)
  const [listError, setListError] = useState<string | null>(null)
  const [fetched, setFetched] = useState<Fetched | null>(null)

  useEffect(() => {
    let cancelled = false
    fetch(`${API}/staged-runs`)
      .then(async (r) => {
        if (!r.ok) throw new Error(r.statusText)
        return (await r.json()) as StagedRunSummary[]
      })
      .then((list) => {
        if (!cancelled) setRuns(list)
      })
      .catch((caught) => {
        if (!cancelled) setListError(message(caught))
      })
    return () => {
      cancelled = true
    }
  }, [])

  const selected = issue !== undefined && repeat !== undefined ? runHash(issue, repeat) : null

  useEffect(() => {
    if (issue === undefined || repeat === undefined) return
    const key = runHash(issue, repeat)
    let cancelled = false
    fetch(`${API}/staged-runs/${issue}/${repeat}`)
      .then(async (r) => {
        if (cancelled) return
        if (r.status === 404) {
          setFetched({ key, kind: 'missing' })
          return
        }
        if (!r.ok) throw new Error(r.statusText)
        const run = (await r.json()) as StagedRunDetail
        if (!cancelled) setFetched({ key, kind: 'ok', run })
      })
      .catch((caught) => {
        if (!cancelled) setFetched({ key, kind: 'error', message: message(caught) })
      })
    return () => {
      cancelled = true
    }
  }, [issue, repeat])

  // No selection, or a fetch still out for it: derived here, not stored.
  const detail = selected === null ? null : fetched !== null && fetched.key === selected ? fetched : undefined

  return (
    <main className="runs-page">
      <h1>Runs</h1>
      <p className="lede">
        Every stored staged val run, stage by stage, ending with the agent's patch
        next to the real fix. Read from the eval records: no model call.
      </p>

      {listError !== null && (
        <section className="error">
          <h2>Failed</h2>
          <p className="mono">{listError}</p>
        </section>
      )}

      {runs !== null && runs.length === 0 && (
        <p className="empty">No stored runs — run the 06a/06b evals to populate data/runs/.</p>
      )}

      {runs !== null && runs.length > 0 && (
        <div className="runs">
          <RunList runs={runs} selected={selected} />
          <div>
            {detail === null && <p className="empty">Pick a run.</p>}
            {detail === undefined && <p className="hint">loading…</p>}
            {detail?.kind === 'missing' && <p className="empty">Run not found.</p>}
            {detail?.kind === 'error' && (
              <section className="error">
                <h2>Failed</h2>
                <p className="mono">{detail.message}</p>
              </section>
            )}
            {detail?.kind === 'ok' && <RunDetail run={detail.run} />}
          </div>
        </div>
      )}
    </main>
  )
}
