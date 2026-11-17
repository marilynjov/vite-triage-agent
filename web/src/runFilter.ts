import type { StagedRunSummary } from './types.generated'

export type RunFilter = { outcome: string; patchedOnly: boolean }

/** `outcome === ''` means any outcome; `patchedOnly` keeps runs with a patch record. */
export function filterRuns(runs: StagedRunSummary[], f: RunFilter): StagedRunSummary[] {
  return runs.filter(
    (r) => (f.outcome === '' || r.outcome === f.outcome) && (!f.patchedOnly || r.patch_outcome !== null),
  )
}
