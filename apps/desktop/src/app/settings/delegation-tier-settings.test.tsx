import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { cleanup, fireEvent, render, screen, within } from '@testing-library/react'
import { afterEach, beforeAll, beforeEach, describe, expect, it, vi } from 'vitest'

import { EMPTY_ROUTE } from '@/app/shell/detached-model-controller'
import type { HermesConfigRecord } from '@/types/hermes'

import { DelegationTierSettings, tierKeys, tierRoute, tierWrites } from './delegation-tier-settings'
import { setNested } from './helpers'

beforeAll(() => {
  Element.prototype.scrollIntoView = vi.fn()
  Element.prototype.hasPointerCapture = vi.fn(() => false)
  Element.prototype.releasePointerCapture = vi.fn()
})

const getGlobalModelOptions = vi.fn()

vi.mock('@/hermes', async importOriginal => ({
  ...(await importOriginal<object>()),
  getGlobalModelOptions: (...args: unknown[]) => getGlobalModelOptions(...args),
  setApiRequestProfile: vi.fn()
}))

beforeEach(() => {
  getGlobalModelOptions.mockResolvedValue({
    providers: [{ models: ['gemini-3.1-pro', 'gemini-2.5-flash'], name: 'Google', slug: 'google' }]
  })
})

afterEach(() => {
  cleanup()
  vi.clearAllMocks()
})

const CONFIG = {
  delegation: {
    model: 'claude-sonnet-5',
    provider: 'anthropic',
    reasoning_effort: 'medium',
    tiers: { easy: { model: 'deepseek-v4-flash', provider: 'deepseek', reasoning_effort: 'low' } }
  }
}

describe('tier config mapping', () => {
  it('normal IS the base delegation block; easy/hard live under delegation.tiers', () => {
    expect(tierKeys('normal')).toEqual({
      effort: 'delegation.reasoning_effort',
      model: 'delegation.model',
      provider: 'delegation.provider'
    })
    expect(tierKeys('hard').model).toBe('delegation.tiers.hard.model')
  })

  it('reads each tier, unset tiers as empty', () => {
    expect(tierRoute(CONFIG, 'normal')).toEqual({ effort: 'medium', model: 'claude-sonnet-5', provider: 'anthropic' })
    expect(tierRoute(CONFIG, 'easy')).toEqual({ effort: 'low', model: 'deepseek-v4-flash', provider: 'deepseek' })
    expect(tierRoute(CONFIG, 'hard')).toEqual(EMPTY_ROUTE)
  })

  it('a YAML false effort reads as thinking off', () => {
    expect(tierRoute({ delegation: { model: 'm', reasoning_effort: false } }, 'normal').effort).toBe('none')
  })

  it('writes round-trip, and clearing blanks all three keys', () => {
    const pick = { effort: 'high', model: 'gpt-6', provider: 'openai' }
    const written = tierWrites('hard', pick).reduce<HermesConfigRecord>((cfg, [k, v]) => setNested(cfg, k, v), CONFIG)

    expect(tierRoute(written, 'hard')).toEqual(pick)
    expect(tierRoute(written, 'easy')).toEqual(tierRoute(CONFIG, 'easy'))
    expect(tierWrites('easy', EMPTY_ROUTE)).toEqual([
      ['delegation.tiers.easy.provider', ''],
      ['delegation.tiers.easy.model', ''],
      ['delegation.tiers.easy.reasoning_effort', '']
    ])
  })
})

describe('DelegationTierSettings', () => {
  function renderSettings() {
    const onChange = vi.fn()
    const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })

    render(
      <QueryClientProvider client={client}>
        <DelegationTierSettings config={CONFIG} onChange={onChange} />
      </QueryClientProvider>
    )

    return onChange
  }

  it('shows each tier and what an unset tier falls back to', () => {
    renderSettings()

    expect(
      within(screen.getByTestId('delegation-tier-normal')).getByText(/anthropic: claude-sonnet-5 · Medium/)
    ).toBeTruthy()
    expect(
      within(screen.getByTestId('delegation-tier-easy')).getByText(/deepseek: deepseek-v4-flash · Low/)
    ).toBeTruthy()
    expect(within(screen.getByTestId('delegation-tier-hard')).getByText('Same as Normal')).toBeTruthy()
  })

  it('picking a model for a tier writes that tier only', async () => {
    const onChange = renderSettings()

    fireEvent.pointerDown(within(screen.getByTestId('delegation-tier-hard')).getByRole('button'), {
      button: 0,
      ctrlKey: false
    })
    fireEvent.click(await screen.findByText(/Gemini 3\.1 Pro/i))

    const writes = onChange.mock.calls.at(-1)![0] as [string, string][]

    expect(writes.map(([k]) => k).every(k => k.startsWith('delegation.tiers.hard.'))).toBe(true)
    expect(Object.fromEntries(writes)).toMatchObject({
      'delegation.tiers.hard.model': 'gemini-3.1-pro',
      'delegation.tiers.hard.provider': 'google'
    })
  })

  it('clear blanks the tier', () => {
    const onChange = renderSettings()

    fireEvent.click(within(screen.getByTestId('delegation-tier-easy')).getByLabelText('Clear'))

    expect(onChange).toHaveBeenCalledWith(tierWrites('easy', EMPTY_ROUTE))
  })
})
