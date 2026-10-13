import { compactNumber } from '@hermes/shared'

import type { AnalyticsTaskEntry } from '@/hermes'

export interface AuxTaskUsageRow {
  key: string
  label: string
  value: string
}

/**
 * Usage rows for the auxiliary-model tasks: `calls · input / output · cache hit`.
 * Input is the whole prompt (cached + uncached) so it reads the same as the
 * session totals; the percentage says how much of it the prompt cache served.
 */
export function auxTaskUsageRows(entries: readonly AnalyticsTaskEntry[] | undefined, limit = 8): AuxTaskUsageRow[] {
  return (entries ?? []).slice(0, limit).map(entry => {
    const prompt = (entry.input_tokens || 0) + (entry.cache_read_tokens || 0)
    const cache = typeof entry.cache_hit_pct === 'number' ? `${entry.cache_hit_pct}%` : '—'

    return {
      key: entry.task,
      label: entry.task,
      value: `${compactNumber(entry.api_calls)} · ${compactNumber(prompt)} / ${compactNumber(entry.output_tokens)} · ${cache}`
    }
  })
}
