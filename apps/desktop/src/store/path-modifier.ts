import { atom } from 'nanostores'

/**
 * Whether the platform's "open in another surface" modifier is held down.
 *
 * Cmd/Ctrl turns file paths in the transcript into openable references, the
 * same gesture an editor uses for go-to-definition. The state is global
 * because the modifier is: several transcript tokens react to one key.
 *
 * The listeners are installed once, by the shell, and never removed.
 */
export const $pathModifierHeld = atom(false)

function isModifier(event: KeyboardEvent) {
  return event.key === 'Meta' || event.key === 'Control'
}

/**
 * Track the modifier on *target* until the returned function is called.
 *
 * Window blur clears the state because a chord that switches applications
 * (Cmd+Tab) delivers keydown and never the matching keyup: without this the
 * transcript would stay armed for the rest of the session. `document`
 * visibility and the browser's own modifier snapshot on any later key event
 * cover the same hole from the other side.
 */
export function watchPathModifier(target: Window = window) {
  const set = (held: boolean) => {
    if ($pathModifierHeld.get() !== held) {
      $pathModifierHeld.set(held)
    }
  }

  const onKeyDown = (event: KeyboardEvent) => set(isModifier(event) || event.metaKey || event.ctrlKey)
  const onKeyUp = (event: KeyboardEvent) => set(!isModifier(event) && (event.metaKey || event.ctrlKey))
  const onRelease = () => set(false)

  target.addEventListener('keydown', onKeyDown)
  target.addEventListener('keyup', onKeyUp)
  target.addEventListener('blur', onRelease)

  return () => {
    target.removeEventListener('keydown', onKeyDown)
    target.removeEventListener('keyup', onKeyUp)
    target.removeEventListener('blur', onRelease)
    set(false)
  }
}
