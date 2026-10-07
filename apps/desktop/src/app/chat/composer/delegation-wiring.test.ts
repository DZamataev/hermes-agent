import { afterEach, describe, expect, it } from 'vitest'

import { buildTileView } from '@/app/chat/session-tile'
import { PRIMARY_SESSION_VIEW } from '@/app/chat/session-view'
import { applyRuntimeInfo } from '@/app/session/hooks/use-session-actions/utils'
import { EMPTY_ROUTE } from '@/app/shell/detached-model-controller'
import { createClientSessionState } from '@/lib/chat-runtime'
import { $draftDelegationOverride } from '@/store/delegation-override'
import { $activeSessionId, $selectedStoredSessionId } from '@/store/session'
import { $sessionStates, $sessionTiles, publishSessionState } from '@/store/session-states'

// The real store wiring behind the Subagents pill — no hand-rolled SessionView.
const PICK = { effort: 'high', model: 'deepseek-v4', provider: 'deepseek' }

afterEach(() => {
  $draftDelegationOverride.set(EMPTY_ROUTE)
  $sessionStates.set({})
  $sessionTiles.set([])
  $activeSessionId.set(null)
  $selectedStoredSessionId.set(null)
})

describe('PRIMARY_SESSION_VIEW.$delegationOverride', () => {
  it('a new-chat draft shows the draft pick', () => {
    $draftDelegationOverride.set(PICK)
    expect(PRIMARY_SESSION_VIEW.$delegationOverride?.get()).toEqual(PICK)
  })

  it("a live session shows its own slice's pick, not the draft's", () => {
    $draftDelegationOverride.set({ effort: '', model: 'draft-model', provider: '' })
    publishSessionState('rt-1', { ...createClientSessionState('stored-1'), delegationOverride: PICK })
    $activeSessionId.set('rt-1')
    $selectedStoredSessionId.set('stored-1')
    expect(PRIMARY_SESSION_VIEW.$delegationOverride?.get()).toEqual(PICK)
  })

  it('a stored session still resuming shows Auto, never the draft pick', () => {
    $draftDelegationOverride.set(PICK)
    $selectedStoredSessionId.set('stored-2')
    expect(PRIMARY_SESSION_VIEW.$delegationOverride?.get()).toEqual(EMPTY_ROUTE)
  })
})

describe('applyRuntimeInfo', () => {
  it('maps a session.create / resume info pick into the slice patch', () => {
    const patch = applyRuntimeInfo({
      delegation_override: { model: 'deepseek-v4', provider: 'deepseek', reasoning_effort: 'high' }
    } as never)

    expect(patch?.delegationOverride).toEqual(PICK)
  })

  it('an Auto answer ({}) clears the slice pick', () => {
    expect(applyRuntimeInfo({ delegation_override: {} } as never)?.delegationOverride).toEqual(EMPTY_ROUTE)
  })
})

describe('buildTileView.$delegationOverride', () => {
  it("reads its own runtime slice and ignores the primary draft", () => {
    $draftDelegationOverride.set({ effort: '', model: 'draft-model', provider: '' })
    const view = buildTileView('stored-t')
    expect(view.$delegationOverride?.get()).toEqual(EMPTY_ROUTE)

    $sessionTiles.set([{ runtimeId: 'rt-t', storedSessionId: 'stored-t' } as never])
    publishSessionState('rt-t', { ...createClientSessionState('stored-t'), delegationOverride: PICK })
    expect(view.$delegationOverride?.get()).toEqual(PICK)
  })
})
