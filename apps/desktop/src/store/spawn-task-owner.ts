import { knownSessionOwner, ownerLookupSessionRows } from './session'
import type { SessionOwnerScope } from './session-request-router'
import { sessionTileOwnerRoute } from './session-states'

/** The offering session's owner, resolved by the ONE ladder both halves of the
 *  result hand-back must agree on: the chip's promise (`notifyHere`) and the
 *  launcher's `spawnReportTo` send are the same decision (Д4/Д5). The tile route
 *  first, then the row's owner; `undefined` = unresolvable, so the result could
 *  not be addressed back and the chip must not promise it. */
export function spawnTaskOwner(storedId: null | string | undefined): SessionOwnerScope {
  return storedId
    ? (sessionTileOwnerRoute(storedId) ?? knownSessionOwner(ownerLookupSessionRows(), storedId))
    : undefined
}

/** Whether that owner resolves — exactly what lets the result come back. */
export function spawnTaskOwnerResolved(storedId: null | string | undefined): boolean {
  return Boolean(spawnTaskOwner(storedId))
}
