import { useStore } from '@nanostores/react'
import { atom } from 'nanostores'
import { useCallback, useEffect, useRef } from 'react'

import type { HermesGateway } from '@/hermes'

/**
 * Ghost-text reply suggestion for the empty composer.
 *
 * When an agent turn completes (busy true → false) and the composer is empty,
 * ask the gateway's deterministic `complete.suggest` for a likely reply and
 * surface the first candidate as ghost text in the placeholder slot. Tab or
 * Right arrow accepts it into real text. It is the turn's suggestion, not the
 * empty composer's: typing (or accepting it) only hides it, and it comes back
 * whenever the composer is empty again, until Esc dismisses it or the next turn
 * replaces it. It is kept per session, so switching to another conversation and back finds it
 * again. Decorative by contract — any RPC failure resolves to "no ghost",
 * never an error, and never blocks the composer.
 *
 * Backend note: `complete.suggest` is the same gateway RPC the Ink TUI uses;
 * `gateway.request` returns the unwrapped result (matching `useSlashCompletions`).
 */

export interface GhostCandidate {
  kind: string
  text: string
}

interface SuggestResult {
  candidates?: GhostCandidate[]
  history_version?: number
}

/** First candidate's text from a `complete.suggest` result, '' when none. */
export function firstGhostCandidate(result: unknown): string {
  const text = (result as SuggestResult | null | undefined)?.candidates?.[0]?.text

  return typeof text === 'string' ? text.trim() : ''
}

/** Whether the ghost should render: only in an idle, empty, undismissed
 *  composer that actually has a suggestion. Pure so the contract is testable. */
export function ghostVisible(o: { busy: boolean; dismissed: boolean; empty: boolean; ghost: string }): boolean {
  return o.empty && !o.busy && !o.dismissed && o.ghost !== ''
}

/** Plain Tab or Right arrow only: a modified key keeps its native meaning. The ghost shows only in
 *  an empty composer, where Right arrow has no caret to move. */
export const isGhostAcceptKey = (e: Pick<KeyboardEvent, 'altKey' | 'ctrlKey' | 'key' | 'metaKey' | 'shiftKey'>) =>
  (e.key === 'Tab' || e.key === 'ArrowRight') && !e.shiftKey && !e.metaKey && !e.ctrlKey && !e.altKey

interface SessionGhost {
  dismissed: boolean
  ghost: string
  /** Token of the session's latest request: a reply to an older one is dropped. */
  seq: number
}

const NO_GHOST: SessionGhost = { dismissed: false, ghost: '', seq: 0 }

/**
 * Each session's suggestion for its last turn. Module-level, not component state: a session
 * switch re-targets the composer (or remounts it in another pane), and the suggestion must
 * survive both.
 */
const $sessionGhosts = atom<Readonly<Record<string, SessionGhost>>>({})

function patchSessionGhost(sessionId: string, patch: Partial<SessionGhost>): void {
  const all = $sessionGhosts.get()

  $sessionGhosts.set({ ...all, [sessionId]: { ...(all[sessionId] ?? NO_GHOST), ...patch } })
}

/** Test seam: forget every session's suggestion. */
export function resetSessionGhosts(): void {
  $sessionGhosts.set({})
}

export function useComposerGhost(opts: {
  busy: boolean
  gateway: HermesGateway | null | undefined
  hasText: boolean
  /**
   * Whether the editor is empty RIGHT NOW, read from the DOM. `hasText` comes from composer state,
   * which lags the editor by a render: a key pressed in the same frame as a typed or pasted
   * character would otherwise still see the ghost and replace the user's own text with it.
   */
  isEditorEmpty: () => boolean
  placeholder: string
  sessionId: null | string | undefined
  /**
   * Makes the accepted ghost text the draft (never sent). It must be the draft's own programmatic
   * write path (`loadIntoComposer` → `paintDraft`, what history recall uses): that mirrors the
   * text, repaints the editor and puts the caret at the end. Setting only the composer state
   * leaves a FOCUSED editor blank, because the state → DOM sync deliberately skips the focused
   * editor: the ghost vanished and nothing appeared in its place.
   */
  writeDraft: (text: string) => void
  /** An open `/` or `@` drawer owns Tab/Escape. */
  trigger: unknown
}): {
  acceptGhost: () => null | string
  dismissGhost: () => boolean
  ghost: string
  /** Tab or Right arrow accepts / Esc dismisses a visible ghost; true when the key was consumed. */
  handleGhostKeyDown: (event: { key: string; nativeEvent: KeyboardEvent; preventDefault: () => void }) => boolean
  /** The ghost while visible, else the regular placeholder. */
  placeholder: string
} {
  const { busy, gateway, hasText, isEditorEmpty, placeholder, sessionId, trigger, writeDraft } = opts
  const empty = !hasText
  const entry = useStore($sessionGhosts)[sessionId ?? ''] ?? NO_GHOST
  const { dismissed, ghost } = entry
  const prevRef = useRef({ busy, sessionId })

  // Fire exactly once per completed turn: on the busy → idle transition OF THE SAME session.
  // Switching from a busy conversation to an idle one is not a turn ending.
  // eslint-disable-next-line no-restricted-syntax -- this records a prop edge, not an atom mirror
  useEffect(() => {
    const prev = prevRef.current
    prevRef.current = { busy, sessionId }

    if (prev.sessionId !== sessionId || !prev.busy || busy) {
      return
    }

    if (!gateway || !sessionId || !empty) {
      return
    }

    const seq = ($sessionGhosts.get()[sessionId]?.seq ?? 0) + 1

    patchSessionGhost(sessionId, { dismissed: false, ghost: '', seq })

    gateway
      .request<SuggestResult>('complete.suggest', { session_id: sessionId })
      .then(result => {
        // Stored under the session that asked, wherever the composer is now.
        if ($sessionGhosts.get()[sessionId]?.seq === seq) {
          patchSessionGhost(sessionId, { ghost: firstGhostCandidate(result) })
        }
      })
      .catch(() => {
        // Decorative: a failed suggestion must never reach the composer.
      })
  }, [busy, empty, gateway, sessionId])

  const visible = ghostVisible({ busy, dismissed, empty, ghost })

  const acceptGhost = useCallback((): null | string => {
    if (!visible) {
      return null
    }

    return ghost
  }, [ghost, visible])

  const dismissGhost = useCallback((): boolean => {
    if (!visible || !sessionId) {
      return false
    }

    patchSessionGhost(sessionId, { dismissed: true })

    return true
  }, [sessionId, visible])

  const handleGhostKeyDown = useCallback(
    (event: { key: string; nativeEvent: KeyboardEvent; preventDefault: () => void }): boolean => {
      if (trigger || !visible || !isEditorEmpty()) {
        return false
      }

      if (isGhostAcceptKey(event.nativeEvent)) {
        // preventDefault keeps Tab from moving focus to the model picker.
        event.preventDefault()
        writeDraft(ghost)

        return true
      }

      if (event.key === 'Escape' && sessionId) {
        event.preventDefault()
        patchSessionGhost(sessionId, { dismissed: true })

        return true
      }

      return false
    },
    [ghost, isEditorEmpty, sessionId, trigger, visible, writeDraft]
  )

  return {
    acceptGhost,
    dismissGhost,
    ghost: visible ? ghost : '',
    handleGhostKeyDown,
    placeholder: visible ? ghost : placeholder
  }
}
