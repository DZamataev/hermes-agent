import { atom } from 'nanostores'

import { EMPTY_ROUTE, type ModelRoute } from '@/app/shell/detached-model-controller'

/**
 * The composer's "Subagents" pick: one forced provider/model/effort for every
 * `delegate_task` child of a session, or Auto (empty model) to let the model's
 * per-task difficulty tier pick from Settings → Subagent models.
 *
 * Session-scoped by design — it is never written to config.yaml. A live
 * session's pick lives on its runtime slice (`ClientSessionState.delegationOverride`,
 * fed by `session.info`); the draft (no session yet) holds it here and ships it
 * on `session.create`.
 */
export const $draftDelegationOverride = atom<ModelRoute>(EMPTY_ROUTE)

export function setDraftDelegationOverride(route: ModelRoute): void {
  $draftDelegationOverride.set(route)
}

/** A gateway `delegation_override` (`{}` / missing model = Auto) as a route. */
export function routeFromWire(raw: unknown): ModelRoute {
  if (!raw || typeof raw !== 'object') {
    return EMPTY_ROUTE
  }

  const value = raw as Record<string, unknown>
  const text = (key: string) => (typeof value[key] === 'string' ? (value[key] as string).trim() : '')
  const model = text('model')

  return model ? { effort: text('reasoning_effort'), model, provider: text('provider') } : EMPTY_ROUTE
}

/** The `config.set key=delegation` / `session.create delegation_override` value for a route. */
export function routeToWire(route: ModelRoute): 'auto' | { model: string; provider: string; reasoning_effort: string } {
  const model = route.model.trim()

  return model ? { model, provider: route.provider.trim(), reasoning_effort: route.effort.trim() } : 'auto'
}

export const sameRoute = (a: ModelRoute, b: ModelRoute): boolean =>
  a.model === b.model && a.provider === b.provider && a.effort === b.effort
