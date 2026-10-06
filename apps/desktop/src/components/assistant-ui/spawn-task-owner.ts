import { useStore } from '@nanostores/react'
import { useMemo } from 'react'

import { useGatewayRequest } from '@/app/gateway/hooks/use-gateway-request'
import { $activeConnectionId } from '@/store/connections'
import { $activeGatewayProfile, normalizeProfileKey } from '@/store/profile'
import { $sessions, knownSessionOwner, ownerLookupSessionRows, sessionMatchesStoredId, sessionPinId } from '@/store/session'
import { isSessionOwnerRoute, requestForSessionProfile, type SessionOwnerScope } from '@/store/session-request-router'
import { $sessionTiles, sessionTileOwnerRoute } from '@/store/session-states'
import { spawnTaskChoiceScope } from '@/store/spawn-task'

type RequestGateway = <T>(method: string, params?: Record<string, unknown>) => Promise<T>

export interface ChipOwner {
  /** Remembered-choice scope (`connection::profile`). */
  choiceScope: string
  /** The owner route's connection, as a tile passes it to its model menu
   *  (undefined = the window's own connection). */
  connectionId?: string
  /** Lineage root of the offering conversation; survives compression. */
  lineageId: null | string
  profile: string
  /** Owner-routed RPC for a routed owner (a tile on another profile or
   *  connection), so the catalog is that backend's; undefined = the window's
   *  own gateway, exactly as the primary chat reads it. */
  request?: RequestGateway
}

/** Where the offering conversation lives — the same owner ladder the launcher
 *  uses (tile route → the row's owner), falling back to the active source. The
 *  chip reads its model catalog from there and remembers its choice under it,
 *  so a model picked in one profile is never offered in another. */
export function useChipOwner(storedId: null | string): ChipOwner {
  const { requestGateway } = useGatewayRequest()
  // Subscriptions only: they re-run the owner lookup when rows or tiles land.
  const sessionRows = useStore($sessions)
  const tiles = useStore($sessionTiles)
  const activeConnectionId = useStore($activeConnectionId)
  const activeProfile = useStore($activeGatewayProfile)

  return useMemo(() => {
    const rows = ownerLookupSessionRows()
    const row = storedId ? rows.find(candidate => sessionMatchesStoredId(candidate, storedId)) : undefined

    const owner: SessionOwnerScope = storedId
      ? (sessionTileOwnerRoute(storedId) ?? knownSessionOwner(rows, storedId))
      : undefined

    const route = isSessionOwnerRoute(owner) ? owner : undefined
    const ownerProfile = route ? route.targetProfile || route.profile : typeof owner === 'string' ? owner : ''
    const profile = normalizeProfileKey(ownerProfile || activeProfile)
    const connectionId = route?.connectionId || undefined

    return {
      // `spawnTaskChoiceScope` maps an unqualified local window to `local`,
      // so one local profile has one remembered pick wherever the chip sits.
      choiceScope: spawnTaskChoiceScope(connectionId || activeConnectionId, profile),
      connectionId,
      lineageId: row ? sessionPinId(row) : storedId,
      profile,
      request: owner
        ? <T,>(method: string, params?: Record<string, unknown>) =>
            requestForSessionProfile<T>(owner, requestGateway, method, params)
        : undefined
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps -- rows/tiles are read through the lookups
  }, [activeConnectionId, activeProfile, requestGateway, sessionRows, storedId, tiles])
}
