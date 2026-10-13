import { describe, expect, it } from 'vitest'

import { auxTaskUsageRows } from './aux-task-usage'

describe('auxTaskUsageRows', () => {
  it('shows calls, whole-prompt input / output and the cache hit rate', () => {
    expect(
      auxTaskUsageRows([
        {
          api_calls: 412,
          cache_hit_pct: 98,
          cache_read_tokens: 12_300_000,
          input_tokens: 180_000,
          models: ['gpt-5.6-sol'],
          output_tokens: 5_100,
          task: 'composer_predictions'
        }
      ])
    ).toEqual([{ key: 'composer_predictions', label: 'composer_predictions', value: '412 · 12.5M / 5.1k · 98%' }])
  })

  it('counts cache writes in the prompt, like the backend percentage does', () => {
    const [row] = auxTaskUsageRows([
      {
        api_calls: 1,
        cache_hit_pct: 50,
        cache_read_tokens: 500,
        cache_write_tokens: 400,
        input_tokens: 100,
        models: [],
        output_tokens: 10,
        task: 'compression'
      }
    ])

    expect(row.value).toBe('1 · 1k / 10 · 50%')
  })

  it('shows a dash when the backend reports no cache rate, and tolerates a missing list', () => {
    expect(
      auxTaskUsageRows([{ api_calls: 1, input_tokens: 10, models: [], output_tokens: 2, task: 'vision' }])[0].value
    ).toBe('1 · 10 / 2 · —')
    expect(auxTaskUsageRows(undefined)).toEqual([])
  })
})
