// The REAL useGatewayRequest (ambient primary) under the REAL tile actions:
// the first prompt.submit frame leaves and the socket drops; useGatewayRequest's
// own transport recovery re-sends on the reopened socket, and that resend is
// refused. The first frame may be running, so the outcome must be `unknown` —
// a `not-sent` would roll the launch back under a live turn.
import { JsonRpcGatewayError, JsonRpcRequestChannel } from '@hermes/shared'
import { act, renderHook } from '@testing-library/react'
import { expect, it, vi } from 'vitest'

import type { HermesGateway } from '@/hermes'

import { MAIN_COMPOSER_SCOPE } from './composer/scope'

const { $gateway, setPrimaryGateway } = await import('@/store/gateway')
const { $activeSessionId, $gatewayState, setSessions } = await import('@/store/session')
const { $sessionTiles, setSessionTileDelegate } = await import('@/store/session-states')
const { useGatewayRequest } = await import('@/app/gateway/hooks/use-gateway-request')
const { useSessionTileActions } = await import('./session-tile-actions')

it.each([
  ['a live owner (4090)', 4090],
  ['session busy (4009)', 4009]
])('a lost first frame resent by useGatewayRequest and refused with %s stays unknown', async (_label, code) => {
  expect(await kickoffOutcome({ firstFrame: 'lost', resend: new JsonRpcGatewayError('refused', { code }) })).toBe('unknown')
})

// The other direction: the first attempt never left (the socket was already
// closed), the resend is refused — nothing reached the backend, so not-sent.
it('a first attempt that never left, resent and refused, is not sent', async () => {
  expect(
    await kickoffOutcome({ firstFrame: 'never-left', resend: new JsonRpcGatewayError('refused', { code: 4090 }) })
  ).toBe('not-sent')
})

async function kickoffOutcome({
  firstFrame,
  resend
}: {
  firstFrame: 'lost' | 'never-left'
  resend: Error
}): Promise<unknown> {
  $activeSessionId.set('foreground-runtime')
  setSessions([])
  $sessionTiles.set([{ runtimeId: 'rt', storedSessionId: 'stored' }])
  setSessionTileDelegate({
    archiveSession: vi.fn(),
    branchSession: vi.fn(),
    deleteSession: vi.fn(),
    executeSlash: vi.fn(),
    interruptSession: vi.fn(),
    resumeTile: vi.fn(async () => 'rt'),
    submitToSession: vi.fn(),
    updateSession: vi.fn((_id, updater) =>
      updater({ attachedImages: [], busy: false, cwd: null, messages: [], model: null, storedSessionId: 'stored' } as never)
    )
  } as never)

  const channel = new JsonRpcRequestChannel({ requestTimeoutMs: 60_000 })
  channel.attach({ send: () => undefined } as never)
  let submits = 0

  const primary = {
    connectionState: 'open',
    request: vi.fn(async (method: string, params?: Record<string, unknown>) => {
      if (method !== 'prompt.submit') {
        return {}
      }

      submits += 1

      if (submits === 1) {
        if (firstFrame === 'never-left') {
          // No transport bound: refused before any frame leaves.
          return await new JsonRpcRequestChannel({ requestTimeoutMs: 60_000 }).request(method, params, undefined, undefined, () => new Error('Hermes gateway connection closed'))
        }

        // The frame LEFT; the socket then drops (same message HermesGateway uses).
        const call = channel.request(method, params)
        channel.detach(new Error('Hermes gateway connection closed'))

        return await call
      }

      throw resend
    })
  }

  setPrimaryGateway(primary as unknown as HermesGateway, 'default')
  $gateway.set(primary as unknown as HermesGateway)
  $gatewayState.set('open')

  const { result } = renderHook(() => {
    const { requestGateway } = useGatewayRequest()

    return useSessionTileActions({ requestGateway, runtimeId: 'rt', scope: MAIN_COMPOSER_SCOPE, storedSessionId: 'stored' })
  })

  let outcome: unknown

  await act(async () => {
    outcome = await result.current.submitLiteralText('fix it')
  })

  expect(submits).toBeGreaterThanOrEqual(2)

  return outcome
}
