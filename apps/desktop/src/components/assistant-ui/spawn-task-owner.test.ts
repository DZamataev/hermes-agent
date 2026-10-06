import { renderHook } from '@testing-library/react'
import { afterEach, describe, expect, it } from 'vitest'

import { $connection, $sessions } from '@/store/session'
import { $sessionTiles } from '@/store/session-states'
import { spawnTaskChoiceScope } from '@/store/spawn-task'
import type { SessionInfo } from '@/types/hermes'

import { useChipOwner } from './spawn-task-owner'

const ROUTE = { connectionId: 'remote-1', profile: 'work', targetProfile: 'work' }

afterEach(() => {
  $sessionTiles.set([])
  $sessions.set([])
})

describe('useChipOwner', () => {
  // A chip in a tile that runs on another backend/profile must read THAT
  // backend's catalog and remember its pick there, not under the window's.
  it('scopes a routed tile chip to the tile owner, not the window', () => {
    $sessionTiles.set([{ ownerRoute: ROUTE, storedSessionId: 'tile-session' } as never])

    const { result } = renderHook(() => useChipOwner('tile-session'))

    expect(result.current.profile).toBe('work')
    expect(result.current.connectionId).toBe('remote-1')
    expect(result.current.choiceScope).toBe(spawnTaskChoiceScope('remote-1', 'work'))
    expect(result.current.request).toBeTypeOf('function')
  })

  // One local profile, one remembered pick: the main chat in an unqualified
  // local window and a tile routed `local` must share it.
  it('scopes an unqualified local window and a local-routed tile alike', () => {
    $sessionTiles.set([{ ownerRoute: { connectionId: 'local', profile: 'default' }, storedSessionId: 'tile' } as never])

    const main = renderHook(() => useChipOwner('main-session')).result.current.choiceScope
    const tile = renderHook(() => useChipOwner('tile')).result.current.choiceScope

    expect(main).toBe(tile)
  })

  // A legacy direct remote (no registry id) is a different machine: its pick
  // must never be offered to (or overwritten by) this machine's profile.
  it('keeps a legacy direct remote window apart from local', () => {
    $connection.set({ baseUrl: 'https://box:9119', mode: 'remote' } as never)

    try {
      const scope = renderHook(() => useChipOwner('main-session')).result.current.choiceScope

      expect(scope).not.toBe(spawnTaskChoiceScope('local', 'default'))
      expect(scope).toBe(spawnTaskChoiceScope('url:https://box:9119', 'default'))
    } finally {
      $connection.set(null)
    }
  })

  it('keys by the lineage root once compression rotates the live id', () => {
    $sessions.set([{ _lineage_root_id: 'root-1', id: 'tip-2' } as SessionInfo])

    const { result } = renderHook(() => useChipOwner('tip-2'))

    expect(result.current.lineageId).toBe('root-1')
  })
})
