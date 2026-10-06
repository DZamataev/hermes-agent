import { renderHook } from '@testing-library/react'
import { afterEach, describe, expect, it } from 'vitest'

import { $sessions } from '@/store/session'
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

  it('keys by the lineage root once compression rotates the live id', () => {
    $sessions.set([{ _lineage_root_id: 'root-1', id: 'tip-2' } as SessionInfo])

    const { result } = renderHook(() => useChipOwner('tip-2'))

    expect(result.current.lineageId).toBe('root-1')
  })
})
