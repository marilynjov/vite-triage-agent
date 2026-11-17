/** Hash routing for two views, no library: `#/` is Triage, `#/runs[/<issue>/<repeat>]` the run browser. */

export type Route = { view: 'triage' } | { view: 'runs'; issue?: number; repeat?: number }

const RUN = /^#\/runs\/(\d+)\/(\d+)$/

export function parseHash(hash: string): Route {
  if (hash === '#/runs') return { view: 'runs' }
  const m = RUN.exec(hash)
  if (m) return { view: 'runs', issue: Number(m[1]), repeat: Number(m[2]) }
  return { view: 'triage' }
}

export function runHash(issue: number, repeat: number): string {
  return `#/runs/${issue}/${repeat}`
}
