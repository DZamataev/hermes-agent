import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { atom } from 'nanostores'
import { afterEach, beforeAll, beforeEach, describe, expect, it, vi } from 'vitest'

import type { ChatBarState } from '@/app/chat/composer/types'
import { type SessionView, SessionViewProvider } from '@/app/chat/session-view'
import { applySessionInfoStatePatch, sessionInfoStatePatch } from '@/app/session/hooks/use-message-stream/utils'
import { DelegationMenuPanel } from '@/app/shell/delegation-menu-panel'
import { EMPTY_ROUTE, type ModelRoute } from '@/app/shell/detached-model-controller'
import { DropdownMenu, DropdownMenuContent } from '@/components/ui/dropdown-menu'
import { createClientSessionState } from '@/lib/chat-runtime'
import { $draftDelegationOverride, routeFromWire, routeToWire } from '@/store/delegation-override'
import { $sessionStates, publishSessionState } from '@/store/session-states'

import { delegationPickLabel, DelegationPill } from './delegation-pill'

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

const CATALOG = { providers: [{ models: ['gemini-3.1-pro', 'gemini-2.5-flash'], name: 'Google', slug: 'google' }] }

beforeEach(() => {
  getGlobalModelOptions.mockResolvedValue(CATALOG)
})

afterEach(() => {
  cleanup()
  vi.clearAllMocks()
  $draftDelegationOverride.set(EMPTY_ROUTE)
  $sessionStates.set({})
})

function view(runtimeId: null | string, $delegationOverride = atom<ModelRoute>(EMPTY_ROUTE)): SessionView {
  return {
    kind: 'tile',
    $awaitingResponse: atom(false),
    $busy: atom(false),
    $cwd: atom(''),
    $delegationOverride,
    $fast: atom(false),
    $lastVisibleIsUser: atom(false),
    $messages: atom([]),
    $messagesEmpty: atom(true),
    $model: atom('parent'),
    $provider: atom('anthropic'),
    $reasoningEffort: atom(''),
    $reasoningEffortPending: atom(false),
    $reasoningEffortWire: atom(''),
    $runtimeId: atom(runtimeId),
    $storedId: atom(runtimeId ? `stored-${runtimeId}` : null),
    $turnStartedAt: atom<number | null>(null)
  }
}

const modelState = (over: Partial<ChatBarState['model']> = {}): ChatBarState['model'] => ({
  canSwitch: true,
  delegationMenuContent: <div>menu</div>,
  model: 'parent',
  provider: 'anthropic',
  ...over
})

describe('wire mapping', () => {
  it('reads {} and a model-less object as Auto', () => {
    expect(routeFromWire({})).toEqual(EMPTY_ROUTE)
    expect(routeFromWire({ provider: 'deepseek' })).toEqual(EMPTY_ROUTE)
    expect(routeFromWire(null)).toEqual(EMPTY_ROUTE)
  })

  it('round-trips a pick and sends Auto as the clear word', () => {
    const pick = { effort: 'high', model: 'deepseek-v4', provider: 'deepseek' }

    expect(routeFromWire(routeToWire(pick))).toEqual(pick)
    expect(routeToWire(EMPTY_ROUTE)).toBe('auto')
  })

  it('session.info carries the pick into the session slice, and a repeat is a no-op', () => {
    const state = createClientSessionState('stored')
    const patch = sessionInfoStatePatch({ delegation_override: { model: 'm', provider: 'p', reasoning_effort: 'low' } })
    const next = applySessionInfoStatePatch(state, patch)

    expect(next.delegationOverride).toEqual({ effort: 'low', model: 'm', provider: 'p' })
    expect(applySessionInfoStatePatch(next, patch)).toBe(next)
    expect(
      applySessionInfoStatePatch(next, sessionInfoStatePatch({ delegation_override: {} })).delegationOverride
    ).toEqual(EMPTY_ROUTE)
  })
})

describe('DelegationPill', () => {
  it('reads Auto until something is forced, then names the model and effort', () => {
    const $pick = atom<ModelRoute>(EMPTY_ROUTE)

    render(
      <SessionViewProvider value={view('rt', $pick)}>
        <DelegationPill disabled={false} model={modelState()} />
      </SessionViewProvider>
    )

    expect(screen.getByTestId('delegation-pill').textContent).toContain('Auto')

    $pick.set({ effort: 'high', model: 'deepseek/deepseek-v4', provider: 'openrouter' })

    return waitFor(() => expect(screen.getByTestId('delegation-pill').textContent).toContain('deepseek-v4 · High'))
  })

  it('hides without a live menu (gateway closed)', () => {
    render(
      <SessionViewProvider value={view('rt')}>
        <DelegationPill disabled={false} model={modelState({ delegationMenuContent: undefined })} />
      </SessionViewProvider>
    )

    expect(screen.queryByTestId('delegation-pill')).toBeNull()
  })

  it('labels drop the vendor prefix and an inherited effort', () => {
    expect(delegationPickLabel('anthropic/claude-sonnet-5', '')).toBe('claude-sonnet-5')
  })
})

function renderPanel(
  runtimeId: null | string,
  write: () => Promise<unknown> = () => Promise.resolve({ value: 'ok' }),
  shape: { kind: SessionView['kind']; storedId: null | string } = { kind: 'primary', storedId: null }
) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  const sessionView = view(runtimeId)

  if (!runtimeId) {
    sessionView.kind = shape.kind
    sessionView.$storedId = atom(shape.storedId)
  }

  // One owner-routed RPC for both the catalog read and the pick write, like a real surface.
  const request = vi.fn((method: string) => (method === 'model.options' ? Promise.resolve(CATALOG) : write()))

  if (runtimeId) {
    // Bind the view to the real store slice so optimistic paint/rollback is observable.
    sessionView.$delegationOverride = atom<ModelRoute>(EMPTY_ROUTE)
    $sessionStates.listen(states => {
      ;(sessionView.$delegationOverride as ReturnType<typeof atom<ModelRoute>>).set(
        states[runtimeId]?.delegationOverride ?? EMPTY_ROUTE
      )
    })
    publishSessionState(runtimeId, createClientSessionState(`stored-${runtimeId}`))
  } else {
    sessionView.$delegationOverride = $draftDelegationOverride
  }

  render(
    <QueryClientProvider client={client}>
      <SessionViewProvider value={sessionView}>
        <DropdownMenu open>
          <DropdownMenuContent>
            <DelegationMenuPanel requestGateway={runtimeId ? (request as never) : undefined} />
          </DropdownMenuContent>
        </DropdownMenu>
      </SessionViewProvider>
    </QueryClientProvider>
  )

  return request
}

describe('DelegationMenuPanel', () => {
  it('a draft keeps the pick locally for session.create — no gateway write', async () => {
    renderPanel(null)
    fireEvent.click(await screen.findByText(/Gemini 3\.1 Pro/i))

    expect($draftDelegationOverride.get()).toMatchObject({ model: 'gemini-3.1-pro', provider: 'google' })
  })

  it('a tile with no runtime yet never writes into the new-chat draft', async () => {
    renderPanel(null, undefined, { kind: 'tile', storedId: 'stored-tile' })
    expect(screen.getByTestId('delegation-auto').getAttribute('data-disabled')).not.toBeNull()
    fireEvent.click(await screen.findByText(/Gemini 3\.1 Pro/i))

    expect($draftDelegationOverride.get()).toEqual(EMPTY_ROUTE)
  })

  it('a stored primary session still resuming never writes into the new-chat draft', async () => {
    renderPanel(null, undefined, { kind: 'primary', storedId: 'stored-resuming' })
    fireEvent.click(await screen.findByText(/Gemini 3\.1 Pro/i))

    expect($draftDelegationOverride.get()).toEqual(EMPTY_ROUTE)
  })

  it('a live session writes the pick through config.set key=delegation with its own session id', async () => {
    const request = renderPanel('rt-1')
    fireEvent.click(await screen.findByText(/Gemini 3\.1 Pro/i))

    await waitFor(() => expect(request).toHaveBeenCalledWith('config.set', expect.anything()))
    expect(request).toHaveBeenCalledWith('config.set', {
      key: 'delegation',
      session_id: 'rt-1',
      value: expect.objectContaining({ model: 'gemini-3.1-pro', provider: 'google' })
    })
    expect($sessionStates.get()['rt-1'].delegationOverride).toMatchObject({ model: 'gemini-3.1-pro' })
  })

  it('Auto clears a live pick', async () => {
    const request = renderPanel('rt-2')
    fireEvent.click(await screen.findByTestId('delegation-auto'))

    await waitFor(() =>
      expect(request).toHaveBeenCalledWith('config.set', { key: 'delegation', session_id: 'rt-2', value: 'auto' })
    )
  })

  it('a failed write rolls the optimistic pick back', async () => {
    const request = renderPanel('rt-3', () => Promise.reject(new Error('boom')))
    fireEvent.click(await screen.findByText(/Gemini 3\.1 Pro/i))

    await waitFor(() => expect(request).toHaveBeenCalledWith('config.set', expect.anything()))
    await waitFor(() => expect($sessionStates.get()['rt-3'].delegationOverride).toEqual(EMPTY_ROUTE))
  })
})
