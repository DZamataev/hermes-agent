import { compactNumber } from '@hermes/shared'

import type { AnalyticsTaskEntry } from '@/hermes'

export interface AuxTaskUsageRow {
  key: string
  label: string
  value: string
}

/**
 * Usage rows for the auxiliary-model tasks: `calls · input / output · cache hit`.
 * Input is the whole prompt (uncached + cache reads + cache writes), the same
 * denominator the backend uses for the percentage the prompt cache served.
 */
export function auxTaskUsageRows(entries: readonly AnalyticsTaskEntry[] | undefined, limit = 8): AuxTaskUsageRow[] {
  return (entries ?? []).slice(0, limit).map(entry => {
    const prompt = (entry.input_tokens || 0) + (entry.cache_read_tokens || 0) + (entry.cache_write_tokens || 0)
    const cache = typeof entry.cache_hit_pct === 'number' ? `${entry.cache_hit_pct}%` : '—'

    return {
      key: entry.task,
      label: entry.task,
      value: `${compactNumber(entry.api_calls)} · ${compactNumber(prompt)} / ${compactNumber(entry.output_tokens)} · ${cache}`
    }
  })
}
