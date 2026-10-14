import { useStore } from '@nanostores/react'
import { useCallback, useEffect } from 'react'

import type { HermesGateway } from '@/hermes'
import { $sessionGhosts, NO_GHOST, patchSessionGhost } from '@/store/composer-ghost'

export { resetSessionGhosts } from '@/store/composer-ghost'

/**
 * Ghost-text reply suggestion for the empty composer.
 *
 * The gateway computes a likely reply when a turn completes (on the session's
 * warm prompt cache) and `complete.suggest` reads it back; the first candidate
 * is shown as ghost text in the placeholder slot of an empty composer. Tab or
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

let readSeq = 0
const nextReadSeq = () => ++readSeq

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
  const stored = useStore($sessionGhosts)[sessionId ?? '']
  const { dismissed, ghost } = stored ?? NO_GHOST
  // No entry: a turn of this session ended since the last read (the event stream drops the entry
  // for every session, shown or not), or this composer never read it.
  const unread = stored === undefined

  // The gateway computes a session's suggestion when its turn completes, whether or not any
  // composer shows it; reading it back is free (no model request), so read whenever it is unread.
  useEffect(() => {
    if (!gateway || !sessionId || busy || !unread) {
      return
    }

    const seq = nextReadSeq()

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
  }, [busy, gateway, sessionId, unread])

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
