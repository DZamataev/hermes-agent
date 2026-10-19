import { atom } from 'nanostores'

/**
 * Each session's composer ghost (the gateway's suggestion for its last turn). Module-level, not
 * component state: a session switch re-targets the composer (or remounts it in another pane), and
 * the suggestion must survive both.
 */
export interface SessionGhost {
  dismissed: boolean
  ghost: string
  /** Token of the session's latest read: a reply to an older one is dropped. */
  seq: number
}

export const NO_GHOST: SessionGhost = { dismissed: false, ghost: '', seq: 0 }

/** A session without an entry has a suggestion the composer has not read yet. */
export const $sessionGhosts = atom<Readonly<Record<string, SessionGhost>>>({})

export function patchSessionGhost(sessionId: string, patch: Partial<SessionGhost>): void {
  const all = $sessionGhosts.get()

  $sessionGhosts.set({ ...all, [sessionId]: { ...(all[sessionId] ?? NO_GHOST), ...patch } })
}

/**
 * A turn of `sessionId` ended: the gateway is computing its new suggestion, so the stored one is
 * stale. Called from the gateway event stream, which sees every session's turns, including those
 * no composer shows; the composer that next shows the session reads the new one.
 */
export function noteSessionTurnEnded(sessionId: string): void {
  const all = $sessionGhosts.get()

  if (!Object.hasOwn(all, sessionId)) {
    return
  }

  const next = { ...all }
  delete next[sessionId]
  $sessionGhosts.set(next)
}

/** Test seam: forget every session's suggestion. */
export function resetSessionGhosts(): void {
  $sessionGhosts.set({})
}

/**
 * Mirrors `display.composer_predictions` (Settings → Chat). While on, a session with history shows
 * no stock follow-up placeholder: the composer stays empty until the prediction arrives.
 */
export const $composerPredictions = atom(true)

export function setComposerPredictionsFromConfig(value: unknown): void {
  $composerPredictions.set(!(value === false || value === 'false' || value === 0))
}
